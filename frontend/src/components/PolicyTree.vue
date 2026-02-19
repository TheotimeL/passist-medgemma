<template>
  <div class="policy-tree">
    <!-- Eligibility Banner -->
    <div class="eligibility-banner" :class="eligibilityClass">
      <div class="elig-top">
        <v-icon size="20" :color="eligibilityColor" class="mr-2">{{ eligibilityIcon }}</v-icon>
        <div class="elig-info">
          <span class="elig-title">{{ eligibilityTitle }}</span>
          <span class="elig-subtitle">{{ eligibilitySubtitle }}</span>
        </div>
      </div>
      <div v-if="blockers.length > 0" class="elig-blockers">
        <span class="blocker-label">Blocking:</span>
        <span v-for="b in blockers" :key="b" class="blocker-name">{{ b }}</span>
      </div>
    </div>

    <!-- Tree Nodes -->
    <div class="tree-content">
      <template v-if="treeData">
        <PolicyTreeNode
          v-for="(child, idx) in treeData.children"
          :key="idx"
          :node="child"
          :results-map="effectiveResultsMap"
          :overrides="overrides"
          :depth="0"
          @view-source="(ev: string, sn?: string) => emit('viewSource', ev, sn)"
        />
      </template>
      <div v-else class="tree-loading">
        <v-progress-circular indeterminate size="20" width="2" color="primary" />
        <span class="ml-2 text-caption text-medium-emphasis">Loading policy tree...</span>
      </div>
    </div>
  </div>
</template>

<script lang="ts">
import type { CriterionOverride } from '@/stores/extraction'

export interface TreeNode {
  name: string
  type: 'ROOT' | 'AND' | 'OR' | 'LEAF'
  id?: string
  source_text?: string
  search_description?: string
  keywords?: string[]
  negated?: boolean
  children?: TreeNode[]
}

export type { CriterionOverride }
</script>

<script lang="ts" setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useExtractionStore } from '@/stores/extraction'
import PolicyTreeNode from './PolicyTreeNode.vue'

const emit = defineEmits<{
  viewSource: [evidence: string, sourceNote?: string]
}>()

const extractionStore = useExtractionStore()
const treeData = ref<TreeNode | null>(null)

// Use store's overrides so top bar stays in sync
const overrides = computed(() => extractionStore.overrides)

const resultsMap = computed(() => {
  const map: Record<string, { met: boolean; evidence: string }> = {}
  for (const r of extractionStore.results) {
    map[r.criterion_id] = { met: r.met, evidence: r.evidence }
  }
  return map
})

// Merge AI results with doctor overrides
const effectiveResultsMap = computed(() => {
  const map = { ...resultsMap.value }
  for (const [id, override] of Object.entries(overrides.value)) {
    map[id] = { met: override.met, evidence: override.evidence }
  }
  return map
})

const overrideCount = computed(() => extractionStore.overrideCount)

// Computed overall status — pushed to store so top bar can use it
const overallStatus = computed(() => computeOverallStatus())

// Sync tree evaluation to the store whenever it changes
watch(overallStatus, (val) => {
  extractionStore.setTreeEligibility(val)
}, { immediate: true })

const eligibilityColor = computed(() => {
  const status = computeOverallStatus()
  if (status === true) return 'success'
  if (status === false) return 'error'
  return 'warning'
})

const eligibilityClass = computed(() => {
  const status = computeOverallStatus()
  if (status === true) return 'elig-pass'
  if (status === false) return 'elig-fail'
  return 'elig-partial'
})

const eligibilityIcon = computed(() => {
  const status = computeOverallStatus()
  if (status === true) return 'mdi-check-decagram'
  if (status === false) return 'mdi-close-octagon'
  return 'mdi-clock-alert-outline'
})

const eligibilityTitle = computed(() => {
  const status = computeOverallStatus()
  if (status === true) return 'Eligible for Authorization'
  if (status === false) return 'Not Eligible'
  return 'Partially Met'
})

const eligibilitySubtitle = computed(() => {
  if (!treeData.value) return ''
  const total = countLeaves(treeData.value)
  const met = countMetLeaves(treeData.value)
  return `${met} of ${total} criteria met`
})

function countLeaves(node: TreeNode): number {
  if (node.type === 'LEAF') return 1
  return (node.children || []).reduce((sum, c) => sum + countLeaves(c), 0)
}

function countMetLeaves(node: TreeNode): number {
  if (node.type === 'LEAF') {
    const id = node.id || ''
    if (overrides.value[id]) return overrides.value[id].met ? 1 : 0
    if (node.negated) return 1
    const r = resultsMap.value[id]
    return r?.met ? 1 : 0
  }
  return (node.children || []).reduce((sum, c) => sum + countMetLeaves(c), 0)
}

function computeOverallStatus(): boolean | null {
  if (!treeData.value) return null
  return evaluateNode(treeData.value)
}

function evaluateNode(node: TreeNode): boolean | null {
  if (node.type === 'LEAF') {
    const id = node.id || ''
    if (overrides.value[id]) return overrides.value[id].met
    if (node.negated) return true
    const r = resultsMap.value[id]
    if (!r) return extractionStore.complete ? false : null // After extraction: no evidence = not met
    return r.met
  }
  const children = node.children || []
  if (children.length === 0) return null
  const childResults = children.map(c => evaluateNode(c))
  if (node.type === 'OR') {
    if (childResults.some(r => r === true)) return true
    if (childResults.every(r => r === false)) return false
    return null
  }
  // AND / ROOT
  if (childResults.every(r => r === true)) return true
  if (childResults.some(r => r === false)) return false
  return null
}

// Find criteria that are blocking eligibility
const blockers = computed(() => {
  if (!treeData.value) return []
  const names: string[] = []
  findBlockers(treeData.value, names)
  return names.slice(0, 5) // Limit to 5 blockers shown
})

function findBlockers(node: TreeNode, names: string[]) {
  if (node.type === 'LEAF') {
    const id = node.id || ''
    const o = overrides.value[id]
    if (o) { if (o.met) return; }
    else {
      if (node.negated) return
      const result = resultsMap.value[id]
      if (result?.met) return
    }
    // This leaf is not met
    const text = node.source_text || node.name || node.id || 'Unknown'
    const shortName = text.replace(/^\([^)]+\)\s*/, '').replace(/^[ivx]+\.\s*/i, '').slice(0, 50)
    names.push(shortName)
    return
  }
  if (node.type === 'OR') {
    const anyMet = node.children?.some(c => {
      if (c.type === 'LEAF') {
        if (c.negated) return true
        const id = c.id || ''
        const o = overrides.value[id]
        if (o) return o.met
        const r = resultsMap.value[id]
        return r?.met
      }
      return false
    })
    if (!anyMet && node.children) {
      const text = node.source_text || node.name || 'Branch'
      names.push(text.replace(/^\([^)]+\)\s*/, '').slice(0, 50))
    }
    return
  }
  for (const child of node.children || []) {
    findBlockers(child, names)
  }
}

onMounted(async () => {
  try {
    const res = await fetch('/api/policy/tree')
    if (res.ok) {
      const data = await res.json()
      treeData.value = data
    }
  } catch {
    // Ignore — tree will show as loading
  }
})
</script>

<style scoped>
.policy-tree {
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* Eligibility Banner */
.eligibility-banner {
  padding: 10px 12px;
  border-radius: 8px;
  margin: 0 8px 8px;
}
.elig-pass { background: #E6F4EA; border: 1px solid #34A853; }
.elig-fail { background: #FCE8E6; border: 1px solid #EA4335; }
.elig-partial { background: #FEF7E0; border: 1px solid #F9AB00; }

.elig-top {
  display: flex;
  align-items: center;
}
.elig-info {
  display: flex;
  flex-direction: column;
  flex: 1;
}
.elig-title {
  font-size: 14px;
  font-weight: 600;
  color: #202124;
  line-height: 1.2;
}
.elig-subtitle {
  font-size: 11px;
  color: #5F6368;
  margin-top: 1px;
}
.elig-overrides {
  display: flex;
  align-items: center;
  font-size: 10px;
  font-weight: 500;
  color: #7B1FA2;
  background: #F3E8FD;
  padding: 2px 8px;
  border-radius: 12px;
  gap: 2px;
  flex-shrink: 0;
}
.clear-overrides {
  font-size: 9px;
  color: #7B1FA2;
  background: none;
  border: none;
  cursor: pointer;
  text-decoration: underline;
  margin-left: 4px;
  padding: 0;
}

.elig-blockers {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid rgba(0,0,0,0.08);
}
.blocker-label {
  font-size: 10px;
  font-weight: 600;
  color: #C5221F;
  text-transform: uppercase;
}
.blocker-name {
  font-size: 10px;
  color: #C5221F;
  background: #FCE8E6;
  padding: 1px 6px;
  border-radius: 4px;
}

/* Tree Content */
.tree-content {
  padding: 0 4px;
}
.tree-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
</style>
