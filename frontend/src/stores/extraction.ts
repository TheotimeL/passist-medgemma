import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export interface ExtractionResult {
  criterion_id: string
  met: boolean
  evidence: string
  drug_name?: string
  drug_dose?: string
  drug_strength?: string
  drug_frequency?: string
  drug_route?: string
  drug_quantity?: string
  drug_days_supply?: string
  drug_dates?: string
  drug_source_text?: string
  is_prior_therapy?: boolean
  failure_reason?: string
  prescriber_name?: string
  prescriber_specialty?: string
  source_note?: string
}

// Collect leaf nodes from the policy tree for building criteria list
type TreeNodeLike = { type: string; id?: string; name?: string; negated?: boolean; children?: TreeNodeLike[] }
interface TreeLeaf { id: string; name: string; negated?: boolean }
function _collectLeaves(node: TreeNodeLike): TreeLeaf[] {
  if (node.type === 'LEAF') {
    return [{ id: node.id || '', name: node.name || node.id || '', negated: node.negated }]
  }
  const leaves: TreeLeaf[] = []
  for (const child of (node.children || [])) {
    leaves.push(..._collectLeaves(child))
  }
  return leaves
}

// OR-aware review status computation
interface ReviewStatus { required: number; reviewed: number }

function _isLeafReviewable(
  node: TreeNodeLike,
  results: ExtractionResult[],
  overridesMap: Record<string, CriterionOverride>,
  extractionComplete: boolean,
): boolean {
  // Negated criteria are always reviewable (auto-met, doctor must confirm)
  if (node.negated) return true
  // Met criteria need accept/reject
  const r = results.find(r => r.criterion_id === (node.id || ''))
  if (r?.met) return true
  // Overridden criteria need review
  const o = overridesMap[node.id || '']
  if (o) return true
  // After extraction completes, unmet criteria also need review (resolve or acknowledge blocker)
  if (extractionComplete) return true
  return false
}

function _computeReviewStatus(
  node: TreeNodeLike,
  results: ExtractionResult[],
  overridesMap: Record<string, CriterionOverride>,
  reviews: Record<string, string>,
  extractionComplete: boolean,
): ReviewStatus {
  if (node.type === 'LEAF') {
    if (!_isLeafReviewable(node, results, overridesMap, extractionComplete)) return { required: 0, reviewed: 0 }
    return { required: 1, reviewed: reviews[node.id || ''] ? 1 : 0 }
  }

  const children = node.children || []
  if (children.length === 0) return { required: 0, reviewed: 0 }

  // AND/ROOT: sum all children
  if (node.type === 'AND' || node.type === 'ROOT') {
    return children.reduce<ReviewStatus>((acc, child) => {
      const s = _computeReviewStatus(child, results, overridesMap, reviews, extractionComplete)
      return { required: acc.required + s.required, reviewed: acc.reviewed + s.reviewed }
    }, { required: 0, reviewed: 0 })
  }

  // OR: pick the "best" branch
  if (node.type === 'OR') {
    const childStatuses = children.map(child =>
      _computeReviewStatus(child, results, overridesMap, reviews, extractionComplete)
    )
    // Prefer a fully-reviewed branch
    const fullyReviewed = childStatuses.filter(s => s.required > 0 && s.reviewed >= s.required)
    if (fullyReviewed.length > 0) {
      return fullyReviewed.reduce((best, s) => s.required < best.required ? s : best)
    }
    // Otherwise pick branch closest to completion (fewest remaining, then fewest total)
    const reviewable = childStatuses
      .filter(s => s.required > 0)
      .sort((a, b) => {
        const remainA = a.required - a.reviewed
        const remainB = b.required - b.reviewed
        if (remainA !== remainB) return remainA - remainB
        return a.required - b.required
      })
    return reviewable.length > 0 ? reviewable[0]! : { required: 0, reviewed: 0 }
  }

  return { required: 0, reviewed: 0 }
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
  const drugFieldsParsing = ref(false)
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

  /** OR-aware review status (picks best branch for OR nodes) */
  const _reviewStatus = computed<ReviewStatus>(() => {
    if (!policyTree.value) return { required: 0, reviewed: 0 }
    return _computeReviewStatus(
      policyTree.value as TreeNodeLike,
      results.value,
      overrides.value,
      criterionReviews.value,
      complete.value,
    )
  })

  /** Number of criteria that have been reviewed (OR-aware) */
  const reviewedCriteriaCount = computed(() => _reviewStatus.value.reviewed)

  /** Total reviewable criteria (OR-aware — only counts the best OR branch) */
  const totalReviewableCriteria = computed(() => _reviewStatus.value.required)

  /** Whether all reviewable criteria have been reviewed */
  const allCriteriaReviewed = computed(() => {
    const { required, reviewed } = _reviewStatus.value
    return required > 0 && reviewed >= required
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

  function reviewEdit(id: string, evidence: string) {
    setOverride(id, true, evidence)
    criterionReviews.value[id] = 'accepted'
  }

  function setTreeEligibility(value: boolean | null) {
    treeEligibility.value = value
  }

  function setPolicyTree(tree: typeof policyTree.value) {
    policyTree.value = tree
  }

  /** Start extraction — requires live model. */
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

    error.value = 'Model not loaded. Start server without SKIP_MODEL=1 or set EXTRACTION_BACKEND=gemini.'
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

    eventSource.addEventListener('complete', async (e: MessageEvent) => {
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

    eventSource.addEventListener('error', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        error.value = data.message
      } catch {
        error.value = 'Connection lost'
      }
      eventSource?.close()
      eventSource = null
      isExtracting.value = false
    })

    // Handle connection errors
    eventSource.onerror = () => {
      eventSource?.close()
      eventSource = null
      if (!complete.value) {
        error.value = 'SSE connection error'
        isExtracting.value = false
      }
    }
  }

  /** Extract structured drug fields from evidence using MedGemma 4B. */
  async function parseDrugFields() {
    const entries = results.value.filter(r => r.evidence && r.evidence !== 'No mention found')
    console.log('[parseDrugFields] entries to parse:', entries.length, entries.map(e => e.criterion_id))
    if (entries.length === 0) return

    drugFieldsParsing.value = true
    try {
      const res = await fetch('/api/form/parse-drug-fields', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entries: entries.map(r => ({ criterion_id: r.criterion_id, evidence: r.evidence, source_note: r.source_note })) }),
      })
      console.log('[parseDrugFields] response status:', res.status)
      if (res.ok) {
        const { entries: parsed } = await res.json()
        console.log('[parseDrugFields] parsed entries:', parsed)
        const _DRUG_FIELDS = ['drug_name', 'drug_strength', 'drug_route', 'drug_frequency', 'drug_dates', 'is_prior_therapy', 'failure_reason'] as const
        for (const parsedEntry of parsed) {
          const match = results.value.find(r => r.criterion_id === parsedEntry.criterion_id)
          if (match) {
            for (const field of _DRUG_FIELDS) {
              const val = parsedEntry[field]
              if (val !== undefined && val !== null && val !== '') {
                ;(match as Record<string, unknown>)[field] = val
              }
            }
            // Map source_text → drug_source_text (the exact snippet mentioning the drug)
            if (parsedEntry.source_text) {
              match.drug_source_text = parsedEntry.source_text
            }
          }
        }
        console.log('[parseDrugFields] results after merge:', results.value.filter(r => r.drug_name).map(r => ({ id: r.criterion_id, drug: r.drug_name, prior: r.is_prior_therapy })))
        // Trigger reactivity — watcher will re-run addExtractionResults
        results.value = [...results.value]
      }
    } catch (e) {
      console.warn('[parseDrugFields] failed:', e)
    } finally {
      drugFieldsParsing.value = false
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
    drugFieldsParsing.value = false
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
    drugFieldsParsing,
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
    reviewEdit,
    reviewUndo,
    fetchExtraction,
    startLiveExtraction,
    cancelExtraction,
    parseDrugFields,
    setOverride,
    clearOverride,
    clearOverrides,
    reset,
  }
})
