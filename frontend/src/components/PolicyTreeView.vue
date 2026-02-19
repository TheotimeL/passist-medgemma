<template>
  <div>
    <!-- Overall Status Card -->
    <div v-if="extractionStore.policyStatus" class="status-card" :class="overallClass">
      <v-icon size="20" :color="overallColor" class="mr-2">{{ overallIcon }}</v-icon>
      <div>
        <span class="text-body-2 font-weight-medium">{{ overallLabel }}</span>
        <span class="text-caption ml-2" style="opacity: 0.8">
          {{ extractionStore.policyStatus.met_count }}/{{ extractionStore.policyStatus.total_count }} criteria met
        </span>
      </div>
    </div>

    <!-- Results by criterion -->
    <div class="criteria-list">
      <div
        v-for="result in extractionStore.results"
        :key="result.criterion_id"
        class="criterion-row"
        :class="{ 'criterion-met': result.met, 'criterion-not-met': !result.met }"
        @click="onCriterionClick(result)"
      >
        <div class="criterion-header">
          <v-icon
            size="16"
            :color="result.met ? 'success' : 'error'"
            class="criterion-icon"
          >
            {{ result.met ? 'mdi-check-circle' : 'mdi-close-circle' }}
          </v-icon>
          <span class="criterion-id">{{ result.criterion_id }}</span>
          <span class="criterion-status" :class="result.met ? 'text-success' : 'text-error'">
            {{ result.met ? 'Met' : 'Not Met' }}
          </span>
          <div class="criterion-actions">
            <v-btn
              icon="mdi-text-search"
              size="x-small"
              variant="text"
              color="primary"
              density="compact"
              @click.stop="onViewEvidence(result)"
            />
            <v-btn
              icon="mdi-shield-outline"
              size="x-small"
              variant="text"
              color="secondary"
              density="compact"
              @click.stop="onViewPolicy(result)"
            />
          </div>
        </div>
        <p
          v-if="result.evidence && result.evidence !== 'No mention found'"
          class="criterion-evidence"
        >
          {{ truncate(result.evidence, 200) }}
        </p>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import { useExtractionStore, type ExtractionResult } from '@/stores/extraction'
import { useHighlightStore } from '@/stores/highlight'

const extractionStore = useExtractionStore()
const highlightStore = useHighlightStore()

const overallColor = computed(() => {
  const ps = extractionStore.policyStatus
  if (!ps) return 'grey'
  if (ps.overall === true) return 'success'
  if (ps.overall === false) return 'error'
  return 'warning'
})

const overallClass = computed(() => {
  const ps = extractionStore.policyStatus
  if (!ps) return ''
  if (ps.overall === true) return 'status-eligible'
  if (ps.overall === false) return 'status-ineligible'
  return 'status-pending'
})

const overallLabel = computed(() => {
  const ps = extractionStore.policyStatus
  if (!ps) return 'Unknown'
  if (ps.overall === true) return 'ELIGIBLE'
  if (ps.overall === false) return 'NOT ELIGIBLE'
  return 'PENDING REVIEW'
})

const overallIcon = computed(() => {
  const ps = extractionStore.policyStatus
  if (!ps) return 'mdi-help-circle'
  if (ps.overall === true) return 'mdi-check-decagram'
  if (ps.overall === false) return 'mdi-close-octagon'
  return 'mdi-clock-outline'
})

function truncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text
  return text.slice(0, maxLen) + '...'
}

function onCriterionClick(result: ExtractionResult) {
  if (result.evidence && result.evidence !== 'No mention found') {
    highlightStore.highlightEvidence(result.evidence, result.criterion_id)
  }
}

function onViewEvidence(result: ExtractionResult) {
  if (result.evidence) {
    highlightStore.highlightEvidence(result.evidence, result.criterion_id)
  }
}

function onViewPolicy(result: ExtractionResult) {
  highlightStore.highlightPolicy(result.criterion_id)
}
</script>

<style scoped>
.status-card {
  display: flex;
  align-items: center;
  padding: 10px 14px;
  border-radius: 8px;
  margin-bottom: 12px;
}
.status-eligible {
  background: #E6F4EA;
  border: 1px solid #34A853;
}
.status-ineligible {
  background: #FCE8E6;
  border: 1px solid #EA4335;
}
.status-pending {
  background: #FEF7E0;
  border: 1px solid #F9AB00;
}

.criteria-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.criterion-row {
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid #E8EAED;
  cursor: pointer;
  transition: all 0.15s ease;
}
.criterion-row:hover {
  background: #F8F9FA;
  border-color: #4285F4;
}

.criterion-header {
  display: flex;
  align-items: center;
  gap: 6px;
}
.criterion-icon {
  flex-shrink: 0;
}
.criterion-id {
  font-size: 11px;
  color: #80868B;
  font-weight: 500;
  flex-shrink: 0;
}
.criterion-status {
  font-size: 13px;
  font-weight: 500;
  flex: 1;
}
.criterion-actions {
  display: flex;
  gap: 0;
  opacity: 0;
  transition: opacity 0.15s ease;
}
.criterion-row:hover .criterion-actions {
  opacity: 1;
}

.criterion-evidence {
  font-size: 12px;
  color: #5F6368;
  margin: 4px 0 0 22px;
  line-height: 1.5;
  font-style: italic;
}
</style>
