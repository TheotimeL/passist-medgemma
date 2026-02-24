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
  highlightTexts?: string[] | null
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

/**
 * Find the best match range for a single search term in the note content.
 * Uses 4 strategies: direct, normalized whitespace, partial (first 40 chars), keyword density.
 */
function findMatchRange(
  searchText: string,
  content: string,
  contentLower: string,
): { start: number; end: number } | null {
  if (!searchText || searchText.length < 5) return null

  const searchNorm = searchText.replace(/\s+/g, ' ').trim().toLowerCase()

  // Strategy 1: Direct case-insensitive search
  const directIdx = contentLower.indexOf(searchNorm)
  if (directIdx !== -1) {
    return { start: directIdx, end: directIdx + searchNorm.length }
  }

  // Strategy 2: Normalized whitespace search
  {
    const normStartMap: number[] = []
    let normPos = 0
    let prevWasSpace = false

    for (let i = 0; i < content.length; i++) {
      const ch = content[i]!
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
      const start = normStartMap[normIdx] ?? 0
      const endNorm = normIdx + searchNorm.length
      const end = normStartMap[endNorm] ?? content.length
      return { start, end }
    }
  }

  // Strategy 3: Partial match (first 40 chars)
  {
    const partial = searchNorm.slice(0, 40)
    const partialIdx = contentLower.indexOf(partial)
    if (partialIdx !== -1) {
      return { start: partialIdx, end: Math.min(content.length, partialIdx + searchText.length) }
    }
  }

  // Strategy 4: Keyword density window
  {
    const stopWords = new Set(['the','a','an','is','was','are','were','be','been','being','have','has','had','do','does','did','will','would','could','should','may','might','shall','can','need','dare','ought','used','to','of','in','for','on','with','at','by','from','as','into','through','during','before','after','above','below','between','out','off','over','under','again','further','then','once','and','but','or','nor','not','so','yet','both','either','neither','each','every','all','any','few','more','most','other','some','such','no','only','own','same','than','too','very','just','because','if','when','where','how','what','which','who','whom','this','that','these','those','it','its','he','she','they','them','his','her','their','my','your','our','i','me','we','you','patient','documented','history','mg','per','day'])
    const words = searchNorm.split(/\s+/).filter(w => w.length >= 3 && !stopWords.has(w))
    const unique = [...new Set(words)]

    if (unique.length >= 3) {
      const positions: { pos: number; word: string }[] = []
      for (const word of unique) {
        let searchFrom = 0
        while (searchFrom < contentLower.length) {
          const idx = contentLower.indexOf(word, searchFrom)
          if (idx === -1) break
          positions.push({ pos: idx, word })
          searchFrom = idx + 1
        }
      }

      if (positions.length >= 3) {
        positions.sort((a, b) => a.pos - b.pos)
        let bestStart = 0
        let bestEnd = 0
        let bestUniqueCount = 0

        for (let i = 0; i < positions.length; i++) {
          const windowStart = positions[i]!.pos
          for (let j = i; j < positions.length; j++) {
            const windowEnd = positions[j]!.pos + positions[j]!.word.length
            const span = windowEnd - windowStart
            if (span > searchNorm.length * 2.5) break

            const uniqueInWindow = new Set(
              positions.slice(i, j + 1).map(p => p.word)
            ).size

            if (uniqueInWindow > bestUniqueCount) {
              bestUniqueCount = uniqueInWindow
              bestStart = windowStart
              bestEnd = windowEnd
            }
          }
        }

        if (bestUniqueCount >= Math.max(3, Math.ceil(unique.length * 0.4))) {
          return { start: bestStart, end: bestEnd }
        }
      }
    }
  }

  return null
}

const highlightedHtml = computed(() => {
  const content = currentNote.value?.content ?? ''
  const escaped = escapeHtml(content)

  // Collect all search terms — prefer highlightTexts (multi-snippet) over single highlightText
  const searchTerms: string[] = []
  if (props.highlightTexts && props.highlightTexts.length > 0) {
    searchTerms.push(...props.highlightTexts.filter(t => t && t.length >= 5))
  } else if (props.highlightText && props.highlightText.length >= 5) {
    searchTerms.push(props.highlightText)
  }

  if (searchTerms.length === 0) return escaped

  const contentLower = content.toLowerCase()

  // Find all match ranges
  const ranges: { start: number; end: number }[] = []
  for (const term of searchTerms) {
    const range = findMatchRange(term, content, contentLower)
    if (range) ranges.push(range)
  }

  if (ranges.length === 0) return escaped

  // Sort by start position, then merge overlapping ranges
  ranges.sort((a, b) => a.start - b.start)
  const merged: { start: number; end: number }[] = [ranges[0]!]
  for (let i = 1; i < ranges.length; i++) {
    const last = merged[merged.length - 1]!
    const curr = ranges[i]!
    if (curr.start <= last.end) {
      last.end = Math.max(last.end, curr.end)
    } else {
      merged.push(curr)
    }
  }

  // Build HTML with multiple <mark> tags
  let result = ''
  let pos = 0
  for (let i = 0; i < merged.length; i++) {
    const { start, end } = merged[i]!
    result += escapeHtml(content.slice(pos, start))
    const markId = i === 0 ? ' id="evidence-highlight"' : ''
    result += `<mark${markId} class="evidence-mark">${escapeHtml(content.slice(start, end))}</mark>`
    pos = end
  }
  result += escapeHtml(content.slice(pos))

  return result
})

function scrollToHighlight() {
  setTimeout(() => {
    const mark = containerRef.value?.querySelector('#evidence-highlight')
    if (mark) {
      mark.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, 200)
}

const hasHighlight = computed(() =>
  (props.highlightTexts && props.highlightTexts.length > 0) || !!props.highlightText
)

// Scroll when highlight changes
watch([() => props.highlightText, () => props.highlightTexts], async () => {
  if (hasHighlight.value) {
    await nextTick()
    scrollToHighlight()
  }
})

// Scroll when note switches and a highlight is active
watch(() => props.selectedIndex, async () => {
  if (hasHighlight.value) {
    await nextTick()
    scrollToHighlight()
  }
})

// Scroll on mount if highlight is already set
onMounted(() => {
  if (hasHighlight.value) {
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
