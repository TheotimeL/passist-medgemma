import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export interface ExtractionResult {
  criterion_id: string
  met: boolean
  evidence: string
  drug_name?: string
  drug_dose?: string
  drug_dates?: string
  is_prior_therapy?: boolean
  failure_reason?: string
  prescriber_name?: string
  prescriber_specialty?: string
  source_note?: string
}

// Collect leaf nodes from the policy tree for building criteria list
interface TreeLeaf { id: string; name: string; negated?: boolean }
function _collectLeaves(node: { type: string; id?: string; name?: string; negated?: boolean; children?: unknown[] }): TreeLeaf[] {
  if (node.type === 'LEAF') {
    return [{ id: node.id || '', name: node.name || node.id || '', negated: node.negated }]
  }
  const leaves: TreeLeaf[] = []
  for (const child of (node.children || []) as typeof node[]) {
    leaves.push(..._collectLeaves(child))
  }
  return leaves
}

export interface PolicyStatus {
  overall: boolean | null
  met_count: number
  total_count: number
  pending_count: number
  criteria: Array<{
    id: string
    name: string
    status: string
    evidence: string
  }>
}

export interface CriterionOverride {
  met: boolean
  evidence: string
}

export const useExtractionStore = defineStore('extraction', () => {
  const isExtracting = ref(false)
  const progress = ref('')
  const results = ref<ExtractionResult[]>([])
  const policyStatus = ref<PolicyStatus | null>(null)
  const error = ref<string | null>(null)
  const complete = ref(false)
  const modelLoaded = ref(false)
  const overrides = ref<Record<string, CriterionOverride>>({})
  /** Doctor review state per criterion: 'accepted' or 'rejected' */
  const criterionReviews = ref<Record<string, 'accepted' | 'rejected'>>({})
  /** Tree-evaluated eligibility (set by PolicyTree component). Respects AND/OR logic + overrides. */
  const treeEligibility = ref<boolean | null>(null)
  /** Reference to the full policy tree (set by PolicyTree component) for computing reviewable criteria */
  const policyTree = ref<{ type: string; id?: string; negated?: boolean; children?: unknown[] } | null>(null)
  let eventSource: EventSource | null = null

  /** Number of doctor overrides active */
  const overrideCount = computed(() => Object.keys(overrides.value).length)

  /** Number of criteria that have been reviewed (accepted or rejected) */
  const reviewedCriteriaCount = computed(() => Object.keys(criterionReviews.value).length)

  /** Total reviewable criteria: met + auto-met (things the doctor should confirm) */
  const totalReviewableCriteria = computed(() => {
    if (!policyTree.value) return 0
    const leaves = _collectLeaves(policyTree.value as Parameters<typeof _collectLeaves>[0])
    return leaves.filter(leaf => {
      if (leaf.negated) return true // auto-met
      const r = results.value.find(r => r.criterion_id === leaf.id)
      if (r?.met) return true // AI-met
      // Also count overrides that mark as met
      const o = overrides.value[leaf.id]
      if (o?.met) return true
      return false
    }).length
  })

  /** Whether all reviewable criteria have been reviewed */
  const allCriteriaReviewed = computed(() => {
    if (totalReviewableCriteria.value === 0) return false
    return reviewedCriteriaCount.value >= totalReviewableCriteria.value
  })

  /** Effective met count including overrides */
  const effectiveMetCount = computed(() => {
    if (!policyStatus.value) return 0
    let count = policyStatus.value.met_count
    for (const [id, override] of Object.entries(overrides.value)) {
      const aiResult = results.value.find(r => r.criterion_id === id)
      // Check policyStatus criteria for negated/auto-met criteria not in AI results
      const psCriterion = policyStatus.value.criteria.find(c => c.id === id)
      const wasMet = aiResult?.met ?? (psCriterion?.status === 'met' || false)
      if (override.met && !wasMet) count++
      else if (!override.met && wasMet) count--
    }
    return Math.max(0, count)
  })

  function setOverride(criterionId: string, met: boolean, evidence: string) {
    overrides.value[criterionId] = { met, evidence }
  }

  function clearOverride(criterionId: string) {
    delete overrides.value[criterionId]
  }

  function clearOverrides() {
    overrides.value = {}
  }

  function reviewAccept(id: string) {
    criterionReviews.value[id] = 'accepted'
  }

  function reviewReject(id: string) {
    criterionReviews.value[id] = 'rejected'
    setOverride(id, false, '')
  }

  function reviewUndo(id: string) {
    delete criterionReviews.value[id]
    clearOverride(id)
  }

  function setTreeEligibility(value: boolean | null) {
    treeEligibility.value = value
  }

  function setPolicyTree(tree: typeof policyTree.value) {
    policyTree.value = tree
  }

  /** Start extraction — always try live SSE first, fall back to pre-computed. */
  async function fetchExtraction(uuid: string) {
    isExtracting.value = true
    progress.value = 'Starting extraction...'
    error.value = null
    complete.value = false
    results.value = []
    policyStatus.value = null

    // Check if model is loaded for live extraction
    try {
      const healthRes = await fetch('/api/health')
      const health = await healthRes.json()
      modelLoaded.value = health.model_loaded
    } catch {
      modelLoaded.value = false
    }

    if (modelLoaded.value) {
      startLiveExtraction(uuid)
      return
    }

    // Model not loaded — fall back to pre-computed results
    try {
      const res = await fetch(`/api/patients/${uuid}/extraction`)
      if (res.ok) {
        const data = await res.json()
        results.value = data.met_criteria || []
        // Build criteria list from met_criteria for the justification sidebar
        const builtCriteria = (data.met_criteria || []).map((c: ExtractionResult) => ({
          id: c.criterion_id,
          name: c.criterion_id,
          status: c.met ? 'met' : 'not_met',
          evidence: c.evidence || '',
        }))
        // Also fetch policy tree to get all criteria (including not-met ones)
        try {
          const treeRes = await fetch('/api/policy/tree')
          if (treeRes.ok) {
            const tree = await treeRes.json()
            const allLeaves = _collectLeaves(tree)
            const metIds = new Set(builtCriteria.map((c: { id: string }) => c.id))
            for (const leaf of allLeaves) {
              if (!metIds.has(leaf.id)) {
                builtCriteria.push({
                  id: leaf.id,
                  name: leaf.name || leaf.id,
                  status: leaf.negated ? 'met' : 'not_met',
                  evidence: '',
                })
              } else {
                // Update name from tree
                const existing = builtCriteria.find((c: { id: string }) => c.id === leaf.id)
                if (existing) existing.name = leaf.name || leaf.id
              }
            }
          }
        } catch { /* ignore */ }
        policyStatus.value = {
          overall: data.eligible,
          met_count: data.met_count,
          total_count: data.total_count,
          pending_count: data.total_count - data.met_count,
          criteria: builtCriteria,
        }
        complete.value = true
        progress.value = `Complete! ${data.met_count}/${data.total_count} criteria met (pre-computed)`
        isExtracting.value = false
        return
      }
    } catch {
      // Pre-computed not available either
    }

    error.value = 'Model not loaded and no pre-computed results available. Start server without SKIP_MODEL=1.'
    progress.value = ''
    isExtracting.value = false
  }

  /** Start SSE extraction stream. */
  function startLiveExtraction(uuid: string) {
    isExtracting.value = true
    progress.value = 'Starting MedGemma extraction...'

    eventSource = new EventSource(`/api/patients/${uuid}/extract`)

    eventSource.addEventListener('status', (e: MessageEvent) => {
      const data = JSON.parse(e.data)
      progress.value = data.message
    })

    eventSource.addEventListener('run', (e: MessageEvent) => {
      const data = JSON.parse(e.data)
      results.value = data.criteria || []
      const noteLabel = data.note ? ` (${data.note})` : ''
      progress.value = `Note ${data.run} complete${noteLabel} (${data.time_s}s) — ${data.criteria?.length || 0} criteria found`
    })

    eventSource.addEventListener('policy', (e: MessageEvent) => {
      const data = JSON.parse(e.data)
      policyStatus.value = data
      progress.value = `Policy evaluated: ${data.met_count}/${data.total_count} criteria met`
    })

    eventSource.addEventListener('complete', (e: MessageEvent) => {
      const data = JSON.parse(e.data)
      results.value = data.met_criteria || []
      policyStatus.value = {
        overall: data.eligible,
        met_count: data.met_count,
        total_count: data.total_count,
        pending_count: data.total_count - data.met_count,
        criteria: policyStatus.value?.criteria || [],
      }
      complete.value = true
      isExtracting.value = false
      progress.value = `Complete! ${data.met_count}/${data.total_count} criteria met (${data.inference_time_s}s)`
      eventSource?.close()
      eventSource = null
    })

    eventSource.addEventListener('error', async (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        error.value = data.message
      } catch {
        error.value = 'Connection lost'
      }
      eventSource?.close()
      eventSource = null

      // Fall back to pre-computed results
      try {
        const res = await fetch(`/api/patients/${uuid}/extraction`)
        if (res.ok) {
          const data = await res.json()
          results.value = data.met_criteria || []
          policyStatus.value = {
            overall: data.eligible,
            met_count: data.met_count,
            total_count: data.total_count,
            pending_count: data.total_count - data.met_count,
            criteria: [],
          }
          complete.value = true
          error.value = null
          progress.value = `Complete! ${data.met_count}/${data.total_count} criteria met (pre-computed)`
        }
      } catch {
        // Pre-computed not available either
      }
      isExtracting.value = false
    })

    // Handle connection errors
    eventSource.onerror = async () => {
      eventSource?.close()
      eventSource = null
      if (!complete.value) {
        // Fall back to pre-computed results
        try {
          const res = await fetch(`/api/patients/${uuid}/extraction`)
          if (res.ok) {
            const data = await res.json()
            results.value = data.met_criteria || []
            policyStatus.value = {
              overall: data.eligible,
              met_count: data.met_count,
              total_count: data.total_count,
              pending_count: data.total_count - data.met_count,
              criteria: [],
            }
            complete.value = true
            progress.value = `Complete! ${data.met_count}/${data.total_count} criteria met (pre-computed)`
          } else {
            error.value = 'SSE connection error and no pre-computed results available'
          }
        } catch {
          error.value = 'SSE connection error'
        }
        isExtracting.value = false
      }
    }
  }

  function cancelExtraction() {
    eventSource?.close()
    eventSource = null
    isExtracting.value = false
    progress.value = 'Extraction cancelled'
  }

  function reset() {
    cancelExtraction()
    progress.value = ''
    results.value = []
    policyStatus.value = null
    error.value = null
    complete.value = false
    overrides.value = {}
    criterionReviews.value = {}
    treeEligibility.value = null
    policyTree.value = null
  }

  return {
    isExtracting,
    progress,
    results,
    policyStatus,
    error,
    complete,
    modelLoaded,
    overrides,
    overrideCount,
    effectiveMetCount,
    criterionReviews,
    reviewedCriteriaCount,
    totalReviewableCriteria,
    allCriteriaReviewed,
    policyTree,
    treeEligibility,
    setTreeEligibility,
    setPolicyTree,
    reviewAccept,
    reviewReject,
    reviewUndo,
    fetchExtraction,
    startLiveExtraction,
    cancelExtraction,
    setOverride,
    clearOverride,
    clearOverrides,
    reset,
  }
})
