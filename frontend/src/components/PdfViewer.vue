<template>
  <div ref="containerRef" class="pdf-viewer">
    <div v-if="loading" class="pdf-loading">
      <v-progress-circular indeterminate size="32" color="primary" />
      <span class="mt-3 text-caption text-medium-emphasis">Loading PDF...</span>
    </div>
    <div v-if="error" class="pdf-loading">
      <v-icon size="40" color="error">mdi-alert-circle-outline</v-icon>
      <span class="mt-3 text-caption text-error">{{ error }}</span>
    </div>
    <div
      v-for="page in pages"
      :key="page.num"
      :ref="el => setPageRef(page.num, el as HTMLElement)"
      class="pdf-page-wrapper"
    >
      <canvas :ref="el => setCanvasRef(page.num, el as HTMLCanvasElement)" class="pdf-canvas" />
      <!-- Field overlay layer -->
      <div class="pdf-overlay" :style="{ width: page.width + 'px', height: page.height + 'px' }">
        <div
          v-for="field in page.fields"
          :key="field.pdfFieldName"
          class="pdf-field-overlay"
          :class="[
            field.isMapped
              ? 'pdf-field-' + (fieldStatuses[field.fieldId] || 'empty')
              : field.hasPdfValue ? 'pdf-field-filled' : 'pdf-field-unfilled',
            { 'pdf-field-active': activeFieldId === field.fieldId },
          ]"
          :style="field.style"
          :title="field.label"
          @click="field.isMapped ? onFieldClick(field.fieldId) : undefined"
        />
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import * as pdfjsLib from 'pdfjs-dist'

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

interface FieldOverlay {
  fieldId: string
  label: string
  pdfFieldName: string
  isMapped: boolean
  hasPdfValue: boolean
  style: Record<string, string>
}

interface PageInfo {
  num: number
  width: number
  height: number
  fields: FieldOverlay[]
}

const props = defineProps<{
  pdfData: ArrayBuffer | null
  fieldIdToPdfName: Record<string, string[]>
  fieldStatuses: Record<string, string>
}>()

const emit = defineEmits<{
  fieldClick: [fieldId: string]
  loaded: []
}>()

const containerRef = ref<HTMLElement | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const pages = ref<PageInfo[]>([])
const activeFieldId = ref<string | null>(null)
const canvasRefs = ref<Record<number, HTMLCanvasElement>>({})
const pageRefs = ref<Record<number, HTMLElement>>({})
let pdfDoc: pdfjsLib.PDFDocumentProxy | null = null
const SCALE = 1.0

// Reverse map: PDF field name → fieldId
let pdfNameToFieldId: Record<string, string> = {}

function setCanvasRef(pageNum: number, el: HTMLCanvasElement | null) {
  if (el) canvasRefs.value[pageNum] = el
}

function setPageRef(pageNum: number, el: HTMLElement | null) {
  if (el) pageRefs.value[pageNum] = el
}

function buildReverseMap() {
  pdfNameToFieldId = {}
  for (const [fieldId, pdfNames] of Object.entries(props.fieldIdToPdfName)) {
    for (const name of pdfNames) {
      pdfNameToFieldId[name] = fieldId
    }
  }
}

async function loadPdf(data: ArrayBuffer) {
  loading.value = true
  error.value = null
  pages.value = []

  try {
    const loadingTask = pdfjsLib.getDocument({ data: new Uint8Array(data) })
    pdfDoc = await loadingTask.promise

    buildReverseMap()

    const pageInfos: PageInfo[] = []
    for (let i = 1; i <= pdfDoc.numPages; i++) {
      const page = await pdfDoc.getPage(i)
      const viewport = page.getViewport({ scale: SCALE })

      pageInfos.push({
        num: i,
        width: viewport.width,
        height: viewport.height,
        fields: [],
      })
    }

    pages.value = pageInfos
    await nextTick()

    // Render canvases and collect annotations
    for (let i = 1; i <= pdfDoc.numPages; i++) {
      const page = await pdfDoc.getPage(i)
      const viewport = page.getViewport({ scale: SCALE })
      const canvas = canvasRefs.value[i]
      if (!canvas) continue

      const dpr = window.devicePixelRatio || 1
      canvas.width = viewport.width * dpr
      canvas.height = viewport.height * dpr
      canvas.style.width = viewport.width + 'px'
      canvas.style.height = viewport.height + 'px'
      const ctx = canvas.getContext('2d')!
      ctx.scale(dpr, dpr)
      await page.render({ canvasContext: ctx, viewport, canvas } as any).promise

      // Parse annotations for field overlays
      const annotations = await page.getAnnotations()
      const fields: FieldOverlay[] = []

      for (const annot of annotations) {
        if (!annot.fieldName) continue
        const fieldId = pdfNameToFieldId[annot.fieldName]
        const isMapped = !!fieldId
        const hasPdfValue = !!annot.fieldValue

        // Convert PDF rect to viewport coordinates
        const rect = annot.rect // [x1, y1, x2, y2] in PDF coordinates
        const [x1, y1, x2, y2] = pdfjsLib.Util.normalizeRect(
          viewport.convertToViewportRectangle(rect),
        )

        fields.push({
          fieldId: fieldId || annot.fieldName,
          label: fieldId ? fieldId.replace(/_/g, ' ') : annot.fieldName,
          pdfFieldName: annot.fieldName,
          isMapped,
          hasPdfValue,
          style: {
            left: x1 + 'px',
            top: y1 + 'px',
            width: (x2 - x1) + 'px',
            height: (y2 - y1) + 'px',
          },
        })
      }

      pages.value[i - 1]!.fields = fields
    }

    emit('loaded')

    // Default to page 2
    await nextTick()
    scrollToPage(2)
  } catch (e: any) {
    error.value = e.message || 'Failed to load PDF'
  } finally {
    loading.value = false
  }
}

function scrollToPage(pageNum: number) {
  const pageEl = pageRefs.value[pageNum]
  if (pageEl && containerRef.value) {
    pageEl.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

function onFieldClick(fieldId: string) {
  activeFieldId.value = fieldId
  setTimeout(() => { activeFieldId.value = null }, 2000)
  emit('fieldClick', fieldId)
}

watch(() => props.pdfData, (data) => {
  if (data) loadPdf(data)
}, { immediate: true })

watch(() => props.fieldIdToPdfName, () => {
  buildReverseMap()
}, { deep: true })

defineExpose({})
</script>

<style scoped>
.pdf-viewer {
  width: 100%;
  height: 100%;
  overflow-y: auto;
  overflow-x: auto;
  background: #525659;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
}

.pdf-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 200px;
}

.pdf-page-wrapper {
  position: relative;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
  background: #fff;
  flex-shrink: 0;
}

.pdf-canvas {
  display: block;
}

.pdf-overlay {
  position: absolute;
  top: 0;
  left: 0;
  pointer-events: none;
}

.pdf-field-overlay {
  position: absolute;
  pointer-events: auto;
  cursor: pointer;
  border-radius: 2px;
  transition: opacity 0.15s, box-shadow 0.15s;
}

/* Status colors */
.pdf-field-suggested {
  background: rgba(251, 188, 4, 0.2);
  border: 1px solid rgba(251, 188, 4, 0.4);
}
.pdf-field-suggested:hover {
  background: rgba(251, 188, 4, 0.35);
}

.pdf-field-accepted {
  background: rgba(52, 168, 83, 0.2);
  border: 1px solid rgba(52, 168, 83, 0.4);
}
.pdf-field-accepted:hover {
  background: rgba(52, 168, 83, 0.35);
}

.pdf-field-rejected {
  background: rgba(234, 67, 53, 0.2);
  border: 1px solid rgba(234, 67, 53, 0.4);
}
.pdf-field-rejected:hover {
  background: rgba(234, 67, 53, 0.35);
}

.pdf-field-edited {
  background: rgba(66, 133, 244, 0.2);
  border: 1px solid rgba(66, 133, 244, 0.4);
}
.pdf-field-edited:hover {
  background: rgba(66, 133, 244, 0.35);
}

.pdf-field-empty {
  background: transparent;
  border: 1px dashed rgba(128, 128, 128, 0.2);
}
.pdf-field-empty:hover {
  background: rgba(128, 128, 128, 0.1);
  border-color: rgba(128, 128, 128, 0.4);
}

/* Unmapped but filled by backend (computed fields like gender, split address) */
.pdf-field-filled {
  background: rgba(52, 168, 83, 0.1);
  border: 1px solid rgba(52, 168, 83, 0.25);
  pointer-events: none;
}

/* Unmapped and empty */
.pdf-field-unfilled {
  background: transparent;
  border: 1px dashed rgba(128, 128, 128, 0.12);
  pointer-events: none;
}

/* Active/highlighted field — pulse animation */
.pdf-field-active {
  animation: field-pulse 0.6s ease-in-out 3;
  z-index: 10;
}

@keyframes field-pulse {
  0%, 100% {
    box-shadow: 0 0 0 0 rgba(66, 133, 244, 0.6);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(66, 133, 244, 0.3);
  }
}
</style>
