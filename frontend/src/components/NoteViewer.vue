<template>
  <div class="note-viewer-root">
    <!-- Note selector dropdown (only shown when multiple notes exist) -->
    <div v-if="notes.length > 1" class="note-selector">
      <v-select
        :model-value="selectedIndex"
        :items="noteItems"
        item-title="label"
        item-value="index"
        density="compact"
        variant="outlined"
        hide-details
        class="note-select"
        @update:model-value="(val: number) => emit('update:selectedIndex', val)"
      />
    </div>

    <!-- Note content -->
    <div ref="containerRef" class="note-content-scroll">
      <div
        v-if="currentNote"
        class="note-content"
        v-html="highlightedHtml"
      />
      <div v-else class="note-empty">
        No note selected
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed, watch, ref, nextTick, onMounted } from 'vue'
import type { NoteFile } from '@/stores/patient'

const props = defineProps<{
  notes: NoteFile[]
  selectedIndex: number
  highlightText: string | null
}>()

const emit = defineEmits<{
  'update:selectedIndex': [index: number]
}>()

const containerRef = ref<HTMLElement | null>(null)

const currentNote = computed(() => props.notes[props.selectedIndex] ?? null)

const noteItems = computed(() =>
  props.notes.map((note, index) => ({
    index,
    label: note.title || note.filename,
    subtitle: note.date || '',
  }))
)

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

const highlightedHtml = computed(() => {
  const content = currentNote.value?.content ?? ''
  const escaped = escapeHtml(content)

  if (!props.highlightText || props.highlightText.length < 5) return escaped

  const contentLower = content.toLowerCase()
  const searchNorm = props.highlightText.replace(/\s+/g, ' ').trim().toLowerCase()

  let matchStart = -1
  let matchEnd = -1

  // Strategy 1: Direct case-insensitive search
  const directIdx = contentLower.indexOf(searchNorm)
  if (directIdx !== -1) {
    matchStart = directIdx
    matchEnd = directIdx + searchNorm.length
  }

  // Strategy 2: Normalized whitespace search
  if (matchStart === -1) {
    const normStartMap: number[] = []
    let normPos = 0
    let prevWasSpace = false

    for (let i = 0; i < content.length; i++) {
      const ch = content[i]
      if (/\s/.test(ch)) {
        if (!prevWasSpace) {
          normStartMap[normPos] = i
          normPos++
          prevWasSpace = true
        }
      } else {
        normStartMap[normPos] = i
        normPos++
        prevWasSpace = false
      }
    }

    const contentNorm = content.replace(/\s+/g, ' ').trim().toLowerCase()
    const normIdx = contentNorm.indexOf(searchNorm)
    if (normIdx !== -1) {
      matchStart = normStartMap[normIdx] ?? 0
      const endNorm = normIdx + searchNorm.length
      matchEnd = (normStartMap[endNorm] ?? content.length)
    }
  }

  // Strategy 3: Partial match (first 40 chars)
  if (matchStart === -1) {
    const partial = searchNorm.slice(0, 40)
    const partialIdx = contentLower.indexOf(partial)
    if (partialIdx !== -1) {
      matchStart = partialIdx
      matchEnd = Math.min(content.length, partialIdx + props.highlightText.length)
    }
  }

  if (matchStart === -1) return escaped

  const before = escapeHtml(content.slice(0, matchStart))
  const match = escapeHtml(content.slice(matchStart, matchEnd))
  const after = escapeHtml(content.slice(matchEnd))

  return `${before}<mark id="evidence-highlight" class="evidence-mark">${match}</mark>${after}`
})

function scrollToHighlight() {
  setTimeout(() => {
    const mark = containerRef.value?.querySelector('#evidence-highlight')
    if (mark) {
      mark.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, 200)
}

// Scroll when highlight changes
watch(() => props.highlightText, async () => {
  if (props.highlightText) {
    await nextTick()
    scrollToHighlight()
  }
})

// Scroll when note switches and a highlight is active
watch(() => props.selectedIndex, async () => {
  if (props.highlightText) {
    await nextTick()
    scrollToHighlight()
  }
})

// Scroll on mount if highlight is already set
onMounted(() => {
  if (props.highlightText) {
    scrollToHighlight()
  }
})
</script>

<style scoped>
.note-viewer-root {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.note-selector {
  flex-shrink: 0;
  padding: 8px 0 10px;
}

.note-select {
  font-size: 13px;
}

.note-content-scroll {
  flex: 1 1 0;
  min-height: 0;
  overflow-y: auto;
}

.note-content {
  font-family: 'Roboto', -apple-system, sans-serif;
  font-size: 13.5px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
  color: #3C4043;
}

.note-content :deep(.evidence-mark) {
  background-color: #FBBC05;
  color: #202124;
  padding: 2px 4px;
  border-radius: 3px;
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(251, 188, 5, 0.4);
}

.note-empty {
  color: #80868B;
  font-size: 13px;
  padding: 16px 0;
}
</style>
