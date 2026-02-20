<template>
  <div class="tree-node" :class="nodeClass">
    <!-- Gate node (AND/OR/ROOT) — skip empty gates -->
    <template v-if="node.type !== 'LEAF' && hasVisibleChildren">
      <div class="gate-row" @click="collapsed = !collapsed">
        <v-icon size="14" class="gate-chevron">
          {{ collapsed ? 'mdi-chevron-right' : 'mdi-chevron-down' }}
        </v-icon>
        <span class="gate-badge" :class="'gate-' + node.type.toLowerCase()">
          {{ gateLabel }}
        </span>
        <span class="gate-name">{{ gateName }}</span>
        <span class="gate-counter">{{ gateCounter }}</span>
      </div>
      <div v-if="!collapsed" class="gate-children">
        <PolicyTreeNode
          v-for="(child, idx) in visibleChildren"
          :key="idx"
          :node="child"
          :results-map="resultsMap"
          :overrides="overrides"
          :depth="depth + 1"
          :parent-or-satisfied="node.type === 'OR' && evaluateGate() === true"
          @view-source="(ev: string, sn?: string) => emit('viewSource', ev, sn)"
        />
      </div>
    </template>

    <!-- Leaf node (criterion) -->
    <template v-else-if="node.type === 'LEAF'">
      <div class="leaf-card" :class="leafClass">
        <div class="leaf-header" @click="expanded = !expanded">
          <v-icon size="16" :color="leafIconColor" class="flex-shrink-0 leaf-icon">
            {{ leafIcon }}
          </v-icon>
          <div class="leaf-info">
            <span class="leaf-name">{{ leafName }}</span>
            <span v-if="node.negated" class="negated-badge">AUTO-MET</span>
            <span v-if="isOverridden" class="override-badge">DOCTOR</span>
          </div>
          <!-- Inline action buttons in header -->
          <template v-if="reviewState">
            <span v-if="reviewState === 'accepted'" class="review-badge review-accepted">
              <v-icon size="10" class="mr-1">mdi-check</v-icon>Accepted
            </span>
            <span v-else class="review-badge review-rejected">
              <v-icon size="10" class="mr-1">mdi-close</v-icon>Rejected
            </span>
            <button class="header-action header-undo" @click.stop="undoReview">Undo</button>
          </template>
          <template v-else-if="isBlocker && !showResolve">
            <button
              class="header-action header-resolve"
              @click.stop="showResolve = true; expanded = true"
            >Resolve</button>
          </template>
          <template v-else-if="effectiveMet && !isOverridden && !node.negated">
            <button class="header-action header-accept" @click.stop="acceptReview">Accept</button>
            <button class="header-action header-reject" @click.stop="rejectReview">Reject</button>
          </template>
          <template v-else-if="node.negated && !isOverridden">
            <button class="header-action header-accept" @click.stop="acceptReview">Accept</button>
            <button class="header-action header-override" @click.stop="overrideNegated">Override</button>
          </template>
          <template v-else-if="isOverridden">
            <button class="header-action header-undo" @click.stop="rejectCriterion">Undo</button>
          </template>
          <v-icon size="14" class="expand-chevron">
            {{ expanded ? 'mdi-chevron-up' : 'mdi-chevron-down' }}
          </v-icon>
        </div>

        <!-- Expanded content -->
        <div v-if="expanded" class="leaf-body">
          <!-- Policy requirement text -->
          <div v-if="node.source_text" class="policy-text">
            <v-icon size="11" color="grey" class="mr-1 flex-shrink-0">mdi-file-document-outline</v-icon>
            <span>{{ node.source_text }}</span>
          </div>

          <!-- Evidence (for met criteria) -->
          <div v-if="effectiveMet && effectiveEvidence && effectiveEvidence !== 'No mention found'" class="leaf-evidence">
            <div class="evidence-label">
              <v-icon size="11" color="primary" class="mr-1">mdi-text-search</v-icon>
              <span>Evidence Found</span>
              <span class="evidence-source-badge" :class="isOverridden ? '' : 'evidence-ai'">
                {{ isOverridden ? 'Doctor Override' : 'AI Extracted' }}
              </span>
            </div>
            <p class="evidence-text">{{ effectiveEvidence }}</p>
            <div class="evidence-actions">
              <button class="action-link" @click="emit('viewSource', effectiveEvidence, originalAiResult?.source_note)">
                <v-icon size="12" class="mr-1">mdi-text-search</v-icon>
                View in Notes
              </button>
            </div>
          </div>

          <!-- Negated criterion (auto-met) -->
          <div v-else-if="node.negated && !isOverridden" class="leaf-auto-met">
            <v-icon size="12" color="success" class="mr-1">mdi-information-outline</v-icon>
            <span>Automatically satisfied (absence of disqualifying condition)</span>
          </div>

          <!-- Doctor rejected an AI result — show undo -->
          <div v-else-if="isOverridden && !effectiveMet && originalAiResult?.met" class="rejected-section">
            <div class="rejected-notice">
              <v-icon size="12" color="warning" class="mr-1">mdi-account-cancel</v-icon>
              <span>Rejected by doctor</span>
              <button class="undo-reject-link" @click.stop="rejectCriterion">Undo</button>
            </div>
            <p v-if="originalAiResult?.evidence" class="rejected-evidence">{{ originalAiResult.evidence }}</p>
          </div>

          <!-- No evidence (not met) — read-only guidance -->
          <div v-else class="leaf-guidance">
            <div v-if="isDimmed" class="or-satisfied-note">
              <v-icon size="12" color="grey" class="mr-1">mdi-information-outline</v-icon>
              <span>Not required — another option in this group is already met</span>
            </div>
            <div v-else-if="isBlocker" class="blocker-section">
              <div class="blocker-warning">
                <v-icon size="12" color="error" class="mr-1">mdi-alert</v-icon>
                <span>This criterion is blocking eligibility</span>
                <button v-if="!showResolve" class="resolve-link" @click.stop="showResolve = true">
                  Resolve
                </button>
              </div>
              <div v-if="showResolve" class="resolve-input">
                <textarea
                  v-model="resolveText"
                  class="resolve-textarea"
                  rows="2"
                  placeholder="Provide evidence or justification..."
                  @click.stop
                />
                <div class="resolve-actions">
                  <button class="btn-sm btn-cancel" @click.stop="showResolve = false; resolveText = ''">
                    Cancel
                  </button>
                  <button class="btn-sm btn-save" @click.stop="saveResolve" :disabled="!resolveText.trim()">
                    <v-icon size="12" class="mr-1">mdi-check</v-icon>
                    Mark as Met
                  </button>
                </div>
              </div>
            </div>
            <div v-else class="guidance-tip">
              <v-icon size="12" color="warning" class="mr-1 flex-shrink-0">mdi-lightbulb-outline</v-icon>
              <span>No evidence found in clinical notes</span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed, ref, watch } from 'vue'
import { useExtractionStore } from '@/stores/extraction'
import { useFormStore } from '@/stores/form'
import type { TreeNode, CriterionOverride } from './PolicyTree.vue'

const extractionStore = useExtractionStore()
const formStore = useFormStore()

const props = withDefaults(defineProps<{
  node: TreeNode
  resultsMap: Record<string, { met: boolean; evidence: string }>
  overrides: Record<string, CriterionOverride>
  depth: number
  parentOrSatisfied?: boolean
}>(), {
  parentOrSatisfied: false,
})

const emit = defineEmits<{
  viewSource: [evidence: string, sourceNote?: string]
}>()

const collapsed = ref(false)
const expanded = ref(true)

const result = computed(() => props.resultsMap[props.node.id || ''])
const override = computed(() => props.overrides[props.node.id || ''])

// Original AI result (before overrides) — needed to show undo UI for rejected criteria
const originalAiResult = computed(() => {
  return extractionStore.results.find(r => r.criterion_id === (props.node.id || ''))
})

// Effective state (override takes precedence over everything, including negated)
const effectiveMet = computed(() => {
  if (override.value) return override.value.met
  if (props.node.negated) return true
  return result.value?.met ?? false
})

const effectiveEvidence = computed(() => {
  if (override.value?.evidence) return override.value.evidence
  return result.value?.evidence || ''
})

const isOverridden = computed(() => !!override.value)
const reviewState = computed(() => extractionStore.criterionReviews[props.node.id || ''] as 'accepted' | 'rejected' | undefined)
const showResolve = ref(false)
const resolveText = ref('')

function acceptReview() {
  extractionStore.reviewAccept(props.node.id || '')
  expanded.value = false
}

function rejectReview() {
  extractionStore.reviewReject(props.node.id || '')
}

function undoReview() {
  extractionStore.reviewUndo(props.node.id || '')
}

function rejectCriterion() {
  const id = props.node.id || ''
  if (isOverridden.value) {
    // Undo the override — revert to AI result
    extractionStore.clearOverride(id)
  } else {
    // Override AI result — mark as NOT MET
    extractionStore.setOverride(id, false, '')
  }
}

function overrideNegated() {
  const id = props.node.id || ''
  extractionStore.setOverride(id, false, '')
}

function saveResolve() {
  if (resolveText.value.trim()) {
    const evidence = resolveText.value.trim()
    extractionStore.setOverride(props.node.id || '', true, evidence)

    // Insert into justification letter before the signature block
    if (formStore.justificationText) {
      const criterionLabel = (props.node.source_text || props.node.name || 'criterion')
        .replace(/^\([^)]+\)\s*/, '')
        .replace(/^[ivx]+\.\s*/i, '')
      const newParagraph = `\nRegarding ${criterionLabel.toLowerCase()}: ${evidence}\n`

      // Find signature block ("Sincerely,") and insert before it
      const sigIdx = formStore.justificationText.indexOf('Sincerely,')
      if (sigIdx > 0) {
        formStore.justificationText =
          formStore.justificationText.slice(0, sigIdx) +
          newParagraph + '\n' +
          formStore.justificationText.slice(sigIdx)
      } else {
        // No signature found — append at end
        formStore.justificationText += '\n' + newParagraph
      }
    }

    showResolve.value = false
    resolveText.value = ''
  }
}

// Default expanded for all leaves; collapse dimmed/irrelevant leaves
if (props.node.type === 'LEAF') {
  expanded.value = !(props.parentOrSatisfied && !effectiveMet.value && !props.node.negated)
}

// React to override changes — expand MET leaves, collapse dimmed ones
watch(effectiveMet, (met) => {
  if (props.node.type === 'LEAF') {
    if (met) {
      expanded.value = true
    } else if (isDimmed.value) {
      expanded.value = false
    }
  }
})

// Dynamic collapse for OR branches: when parent OR becomes satisfied, collapse losing branches
watch(() => props.parentOrSatisfied, (orSatisfied) => {
  if (props.node.type === 'LEAF' && orSatisfied && !effectiveMet.value) {
    expanded.value = false
  }
  if (props.node.type !== 'LEAF' && orSatisfied && evaluateGate() !== true) {
    collapsed.value = true
  }
})

// Filter out empty gate nodes
const visibleChildren = computed(() => {
  return (props.node.children || []).filter(c =>
    c.type === 'LEAF' || (c.children && c.children.length > 0)
  )
})
const hasVisibleChildren = computed(() => visibleChildren.value.length > 0)

// --- Gate (AND/OR) computed ---
const gateLabel = computed(() => {
  if (props.node.type === 'AND' || props.node.type === 'ROOT') return 'All required'
  if (props.node.type === 'OR') return 'Any one'
  return ''
})

const gateName = computed(() => {
  const name = props.node.name || ''
  return name.replace(/^\([^)]+\)\s*/, '').slice(0, 60) || 'Requirements'
})

const gateCounter = computed(() => {
  const leaves = countLeaves(props.node)
  const met = countMetLeaves(props.node)
  return `${met}/${leaves}`
})

function countLeaves(node: TreeNode): number {
  if (node.type === 'LEAF') return 1
  return (node.children || []).reduce((sum, c) => sum + countLeaves(c), 0)
}

function countMetLeaves(node: TreeNode): number {
  if (node.type === 'LEAF') {
    const o = props.overrides[node.id || '']
    if (o) return o.met ? 1 : 0
    if (node.negated) return 1
    const r = props.resultsMap[node.id || '']
    return r?.met ? 1 : 0
  }
  return (node.children || []).reduce((sum, c) => sum + countMetLeaves(c), 0)
}


function evaluateGate(): boolean | null {
  const children = props.node.children || []
  if (children.length === 0) return null

  const childResults = children.map(c => {
    if (c.type === 'LEAF') {
      const o = props.overrides[c.id || '']
      if (o) return o.met
      if (c.negated) return true
      const r = props.resultsMap[c.id || '']
      if (!r) return extractionStore.complete ? false : null
      return r.met
    }
    return evaluateGateNode(c)
  })

  if (props.node.type === 'OR') {
    if (childResults.some(r => r === true)) return true
    if (childResults.every(r => r === false)) return false
    return null
  }
  if (childResults.every(r => r === true)) return true
  if (childResults.some(r => r === false)) return false
  return null
}

function evaluateGateNode(node: TreeNode): boolean | null {
  if (node.type === 'LEAF') {
    const o = props.overrides[node.id || '']
    if (o) return o.met
    if (node.negated) return true
    const r = props.resultsMap[node.id || '']
    if (!r) return extractionStore.complete ? false : null
    return r.met
  }
  const children = node.children || []
  const childResults = children.map(c => evaluateGateNode(c))
  if (node.type === 'OR') {
    if (childResults.some(r => r === true)) return true
    if (childResults.every(r => r === false)) return false
    return null
  }
  if (childResults.every(r => r === true)) return true
  if (childResults.some(r => r === false)) return false
  return null
}

// --- Leaf computed ---
const nodeClass = computed(() => ({
  [`depth-${Math.min(props.depth, 3)}`]: true,
}))

const leafClass = computed(() => {
  const classes: string[] = []
  if (props.node.negated) { classes.push('leaf-auto') }
  else if (effectiveMet.value) { classes.push('leaf-met') }
  else { classes.push('leaf-unmet') }
  if (isDimmed.value) classes.push('leaf-dimmed')
  if (reviewState.value === 'accepted') classes.push('leaf-reviewed-accepted')
  if (reviewState.value === 'rejected') classes.push('leaf-reviewed-rejected')
  return classes.join(' ')
})

const leafIcon = computed(() => {
  if (props.node.negated) return 'mdi-check-circle-outline'
  if (effectiveMet.value) return 'mdi-check-circle'
  return 'mdi-close-circle'
})

const leafIconColor = computed(() => {
  if (props.node.negated) return 'success'
  if (effectiveMet.value) return 'success'
  return 'error'
})

const leafName = computed(() => {
  // Use source_text for full description, fall back to name
  if (props.node.source_text) {
    const text = props.node.source_text
    // Remove leading numbering like "(1)", "(a)", "i."
    const cleaned = text.replace(/^\([^)]+\)\s*/, '').replace(/^[ivx]+\.\s*/i, '')
    return cleaned.length > 100 ? cleaned.slice(0, 100) + '...' : cleaned
  }
  return props.node.name || props.node.id || 'Unknown Criterion'
})


const guidanceText = computed(() => {
  if (props.node.search_description) {
    const desc = props.node.search_description
    return desc.length > 200 ? desc.slice(0, 200) + '...' : desc
  }
  return 'No evidence found in clinical notes. Doctor may provide evidence manually.'
})

const isBlocker = computed(() => {
  // Not a blocker if parent OR is already satisfied
  if (props.parentOrSatisfied) return false
  return !effectiveMet.value && !props.node.negated
})

// Dim leaf cards that are in a satisfied OR branch (not required)
const isDimmed = computed(() => {
  return props.parentOrSatisfied && !effectiveMet.value && !props.node.negated
})

</script>

<style scoped>
.tree-node {
  margin-left: 0;
}
.tree-node.depth-1 { margin-left: 12px; }
.tree-node.depth-2 { margin-left: 24px; }
.tree-node.depth-3 { margin-left: 36px; }

/* Gate row */
.gate-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px;
  cursor: pointer;
  border-radius: 4px;
  transition: background 0.1s;
}
.gate-row:hover {
  background: #F1F3F4;
}
.gate-chevron {
  color: #80868B;
  flex-shrink: 0;
}
.gate-badge {
  font-size: 9px;
  font-weight: 600;
  padding: 1px 5px;
  border-radius: 3px;
  letter-spacing: 0.2px;
  flex-shrink: 0;
  white-space: nowrap;
}
.gate-and, .gate-root { color: #1967D2; background: #E8F0FE; }
.gate-or { color: #E65100; background: #FFF3E0; }

.gate-name {
  font-size: 12px;
  font-weight: 500;
  color: #3C4043;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gate-counter {
  font-size: 10px;
  color: #80868B;
  font-weight: 500;
  flex-shrink: 0;
}

.gate-children {
  border-left: 2px solid #E8EAED;
  margin-left: 14px;
  padding-left: 0;
}

/* Leaf card */
.leaf-card {
  margin: 1px 4px;
  padding: 0;
  border-radius: 5px;
  border: 1px solid #E8EAED;
  transition: all 0.15s;
  overflow: hidden;
}
.leaf-met {
  border-left: 3px solid #34A853;
  background: #FCFFFC;
}
.leaf-unmet {
  border-left: 3px solid #EA4335;
  background: #FFFCFC;
}
.leaf-auto {
  border-left: 3px solid #34A853;
  background: #F6FFF8;
}
.leaf-dimmed {
  opacity: 0.5;
  border-left-color: #E8EAED !important;
  background: #FAFAFA !important;
}

.leaf-header {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  padding: 5px 8px;
  cursor: pointer;
  transition: background 0.1s;
}
.leaf-header:hover {
  background: rgba(0,0,0,0.02);
}
.leaf-icon {
  margin-top: 1px;
}
.leaf-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}
.leaf-name {
  font-size: 12px;
  font-weight: 500;
  color: #202124;
  line-height: 1.4;
}
.negated-badge, .override-badge {
  font-size: 8px;
  font-weight: 600;
  padding: 0 4px;
  border-radius: 3px;
  display: inline-block;
  vertical-align: middle;
}
.negated-badge {
  color: #137333;
  background: #E6F4EA;
}
.override-badge {
  color: #7B1FA2;
  background: #F3E8FD;
}


.header-action {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: 4px;
  border: 1px solid;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.1s;
}
.header-resolve {
  color: #1967D2;
  border-color: #1967D2;
  background: #E8F0FE;
}
.header-resolve:hover { background: #D2E3FC; }
.header-reject {
  color: #C5221F;
  border-color: transparent;
  background: none;
}
.header-reject:hover { background: #FCE8E6; border-color: #EA4335; }
.header-undo {
  color: #7B1FA2;
  border-color: transparent;
  background: none;
}
.header-undo:hover { background: #F3E8FD; border-color: #7B1FA2; }
.header-accept {
  color: #137333;
  border-color: #34A853;
  background: #E6F4EA;
}
.header-accept:hover { background: #CEEAD6; }
.header-override {
  color: #5F6368;
  border-color: transparent;
  background: none;
}
.header-override:hover { background: #E8EAED; border-color: #80868B; }

/* Review badges */
.review-badge {
  font-size: 9px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}
.review-accepted { color: #137333; background: #E6F4EA; }
.review-rejected { color: #C5221F; background: #FCE8E6; }

/* Reviewed leaf states */
.leaf-reviewed-accepted .evidence-text {
  border-left-color: #34A853;
  background: #F6FFF8;
}
.leaf-reviewed-rejected .evidence-text {
  text-decoration: line-through;
  opacity: 0.6;
}

.expand-chevron {
  color: #80868B;
  flex-shrink: 0;
  margin-top: 1px;
}

/* Expanded body */
.leaf-body {
  padding: 0 8px 6px 28px;
  border-top: 1px solid #F1F3F4;
}

/* Policy text (source requirement) */
.policy-text {
  display: flex;
  align-items: flex-start;
  font-size: 11px;
  color: #80868B;
  line-height: 1.5;
  padding: 6px 0 4px;
  gap: 2px;
}

/* Evidence */
.leaf-evidence {
  margin-top: 4px;
}
.evidence-label {
  display: flex;
  align-items: center;
  font-size: 10px;
  font-weight: 600;
  color: #1967D2;
  text-transform: uppercase;
  margin-bottom: 4px;
  gap: 4px;
}
.evidence-source-badge {
  font-size: 8px;
  font-weight: 600;
  padding: 1px 5px;
  border-radius: 3px;
  margin-left: auto;
}
.evidence-ai {
  color: #7B1FA2;
  background: #F3E8FD;
}
.evidence-source-badge:not(.evidence-ai) {
  color: #E65100;
  background: #FFF3E0;
}

.evidence-text {
  font-size: 11px;
  color: #3C4043;
  line-height: 1.5;
  font-style: italic;
  margin: 0 0 6px;
  background: #F8F9FA;
  padding: 6px 8px;
  border-radius: 4px;
  border-left: 2px solid #E8EAED;
}
.action-link {
  display: flex;
  align-items: center;
  font-size: 11px;
  color: #1967D2;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
}
.action-link:hover { text-decoration: underline; }
.rejected-section {
  margin-top: 4px;
}
.rejected-notice {
  display: flex;
  align-items: center;
  font-size: 11px;
  color: #B06000;
  gap: 4px;
}
.undo-reject-link {
  font-size: 10px;
  font-weight: 600;
  color: #1967D2;
  background: none;
  border: none;
  cursor: pointer;
  text-decoration: underline;
  margin-left: auto;
  padding: 0;
}
.rejected-evidence {
  font-size: 11px;
  color: #80868B;
  line-height: 1.4;
  margin: 4px 0 0;
  text-decoration: line-through;
  font-style: italic;
}
.evidence-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.reject-link {
  color: #C5221F !important;
}

.action-buttons {
  display: flex;
  gap: 4px;
}

/* Small buttons */
.btn-sm {
  display: flex;
  align-items: center;
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
  cursor: pointer;
  border: 1px solid;
  transition: all 0.1s;
}
.btn-save {
  color: #fff;
  background: #34A853;
  border-color: #34A853;
}
.btn-save:hover { background: #2D9249; }
.btn-cancel {
  color: #5F6368;
  background: #F8F9FA;
  border-color: #E8EAED;
}
.btn-cancel:hover { background: #E8EAED; }
.evidence-textarea {
  width: 100%;
  font-size: 12px;
  font-family: inherit;
  line-height: 1.5;
  padding: 6px 8px;
  border: 1px solid #4285F4;
  border-radius: 4px;
  resize: vertical;
  outline: none;
  background: #fff;
}
.evidence-textarea:focus {
  box-shadow: 0 0 0 2px rgba(66, 133, 244, 0.2);
}
.evidence-edit-actions {
  display: flex;
  gap: 4px;
  margin-top: 4px;
  justify-content: flex-end;
}

/* Auto-met */
.leaf-auto-met {
  display: flex;
  align-items: center;
  margin-top: 4px;
  font-size: 11px;
  color: #80868B;
  font-style: italic;
}

/* Guidance (not met) */
.leaf-guidance {
  margin-top: 4px;
}
.guidance-tip {
  display: flex;
  align-items: flex-start;
  font-size: 11px;
  color: #B06000;
  line-height: 1.4;
  background: #FEF7E0;
  border-radius: 4px;
  padding: 4px 6px;
}
.or-satisfied-note {
  display: flex;
  align-items: center;
  font-size: 10px;
  color: #80868B;
  font-style: italic;
  margin-top: 4px;
}
.blocker-section {
  margin-top: 4px;
}
.blocker-warning {
  display: flex;
  align-items: center;
  font-size: 10px;
  font-weight: 600;
  color: #C5221F;
  padding: 2px 6px;
  background: #FCE8E6;
  border-radius: 4px;
}
.resolve-link {
  font-size: 10px;
  font-weight: 600;
  color: #1967D2;
  background: none;
  border: none;
  cursor: pointer;
  text-decoration: underline;
  margin-left: auto;
  padding: 0;
}
.resolve-input {
  margin-top: 6px;
}
.resolve-textarea {
  width: 100%;
  font-size: 11px;
  font-family: inherit;
  line-height: 1.4;
  padding: 6px 8px;
  border: 1px solid #4285F4;
  border-radius: 4px;
  resize: vertical;
  outline: none;
  background: #fff;
}
.resolve-textarea:focus {
  box-shadow: 0 0 0 2px rgba(66, 133, 244, 0.2);
}
.resolve-actions {
  display: flex;
  gap: 4px;
  margin-top: 4px;
  justify-content: flex-end;
}
</style>
