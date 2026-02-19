<template>
  <div
    class="form-field"
    :class="fieldClass"
  >
    <!-- Pending/Suggested state: needs doctor review -->
    <template v-if="field.status === 'suggested' && field.value">
      <div class="field-header">
        <span class="field-label">{{ field.label }}</span>
        <v-chip
          size="x-small"
          :color="sourceBadgeColor"
          variant="flat"
          class="field-source"
        >
          <v-icon start size="10">{{ sourceBadgeIcon }}</v-icon>
          {{ sourceBadgeText }}
        </v-chip>
      </div>
      <div class="field-body">
        <span class="field-value">{{ field.value }}</span>
        <div class="field-actions">
          <v-btn
            v-if="field.evidence"
            variant="text"
            color="primary"
            size="x-small"
            class="action-btn"
            @click="emit('viewSource')"
          >
            <v-icon start size="14">mdi-text-search</v-icon>
            Source
          </v-btn>
          <v-btn
            variant="tonal"
            color="success"
            size="small"
            class="action-btn"
            @click="emit('accept')"
          >
            <v-icon start size="16">mdi-check</v-icon>
            Accept
          </v-btn>
          <v-btn
            variant="text"
            size="x-small"
            class="action-btn"
            @click="startEditing"
          >
            <v-icon size="14">mdi-pencil</v-icon>
          </v-btn>
          <v-btn
            variant="text"
            color="error"
            size="x-small"
            class="action-btn"
            @click="emit('reject')"
          >
            <v-icon size="14">mdi-close</v-icon>
          </v-btn>
        </div>
      </div>
    </template>

    <!-- Accepted state: settled, minimal -->
    <template v-else-if="field.status === 'accepted'">
      <div class="field-row-compact">
        <v-icon size="14" color="success" class="mr-1 flex-shrink-0">mdi-check-circle</v-icon>
        <span class="field-label-compact">{{ field.label }}</span>
        <span class="field-value-compact">{{ field.value }}</span>
        <v-btn
          v-if="field.evidence"
          icon="mdi-text-search"
          size="x-small"
          variant="text"
          color="primary"
          density="compact"
          @click="emit('viewSource')"
        />
        <v-btn
          icon="mdi-pencil"
          size="x-small"
          variant="text"
          density="compact"
          class="undo-btn"
          @click="startEditing"
        />
      </div>
    </template>

    <!-- Edited state -->
    <template v-else-if="field.status === 'edited'">
      <div class="field-row-compact">
        <v-icon size="14" color="info" class="mr-1 flex-shrink-0">mdi-pencil-circle</v-icon>
        <span class="field-label-compact">{{ field.label }}</span>
        <span class="field-value-compact field-value-edited">{{ field.value }}</span>
        <v-btn
          v-if="field.evidence"
          icon="mdi-text-search"
          size="x-small"
          variant="text"
          color="primary"
          density="compact"
          @click="emit('viewSource')"
        />
      </div>
    </template>

    <!-- Rejected state -->
    <template v-else-if="field.status === 'rejected'">
      <div class="field-row-compact field-row-rejected">
        <v-icon size="14" color="error" class="mr-1 flex-shrink-0">mdi-close-circle</v-icon>
        <span class="field-label-compact">{{ field.label }}</span>
        <span class="field-value-compact text-decoration-line-through text-disabled">{{ field.originalValue || '(empty)' }}</span>
      </div>
    </template>

    <!-- Empty suggested field: no value -->
    <template v-else>
      <div class="field-row-compact field-row-empty">
        <v-icon size="14" color="grey" class="mr-1 flex-shrink-0">mdi-minus-circle-outline</v-icon>
        <span class="field-label-compact">{{ field.label }}</span>
        <span class="text-caption text-disabled font-italic">manual entry needed</span>
        <v-btn
          icon="mdi-pencil"
          size="x-small"
          variant="text"
          density="compact"
          @click="startEditing"
        />
      </div>
    </template>

    <!-- Inline Edit Overlay -->
    <div v-if="editing" class="edit-overlay">
      <v-text-field
        v-model="editValue"
        :label="field.label"
        density="compact"
        variant="outlined"
        hide-details
        autofocus
        class="edit-input"
        @keyup.enter="saveEdit"
        @keyup.escape="cancelEdit"
      >
        <template #append-inner>
          <v-btn icon="mdi-check" size="x-small" variant="text" color="success" @click="saveEdit" />
          <v-btn icon="mdi-close" size="x-small" variant="text" @click="cancelEdit" />
        </template>
      </v-text-field>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed, ref } from 'vue'
import type { FormField } from '@/stores/form'

const props = defineProps<{
  field: FormField
}>()

const emit = defineEmits<{
  accept: []
  reject: []
  edit: [value: string]
  viewSource: []
}>()

const editing = ref(false)
const editValue = ref('')

const fieldClass = computed(() => {
  if (editing.value) return 'field-editing'
  switch (props.field.status) {
    case 'suggested': return props.field.value ? 'field-pending' : 'field-empty'
    case 'accepted': return 'field-accepted'
    case 'rejected': return 'field-rejected'
    case 'edited': return 'field-edited'
    default: return ''
  }
})

const sourceBadgeColor = computed(() => {
  switch (props.field.source) {
    case 'fhir': return 'blue'
    case 'llm': return 'deep-purple'
    case 'inferred': return 'orange'
    default: return 'grey'
  }
})

const sourceBadgeIcon = computed(() => {
  switch (props.field.source) {
    case 'fhir': return 'mdi-database'
    case 'llm': return 'mdi-auto-fix'
    case 'inferred': return 'mdi-lightbulb'
    default: return 'mdi-pencil'
  }
})

const sourceBadgeText = computed(() => {
  switch (props.field.source) {
    case 'fhir': return 'EHR'
    case 'llm': return 'AI'
    case 'inferred': return 'Inferred'
    default: return 'Manual'
  }
})

function startEditing() {
  editValue.value = props.field.value || props.field.originalValue
  editing.value = true
}

function saveEdit() {
  emit('edit', editValue.value)
  editing.value = false
}

function cancelEdit() {
  editing.value = false
}
</script>

<style scoped>
.form-field {
  position: relative;
  border-radius: 8px;
  margin-bottom: 6px;
  transition: all 0.15s ease;
}

/* --- Pending field: needs review --- */
.field-pending {
  background: #FFFBF0;
  border: 1px solid #F9AB00;
  padding: 10px 12px;
  border-left: 4px solid #F9AB00;
}
.field-pending:hover {
  box-shadow: 0 1px 6px rgba(249, 171, 0, 0.15);
}

.field-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.field-label {
  font-size: 11px;
  font-weight: 500;
  color: #80868B;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.field-source {
  height: 18px !important;
  font-size: 10px !important;
}
.field-body {
  display: flex;
  align-items: center;
  gap: 8px;
}
.field-value {
  font-size: 14px;
  font-weight: 500;
  color: #202124;
  flex: 1;
  line-height: 1.4;
}
.field-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}
.action-btn {
  text-transform: none !important;
  letter-spacing: 0 !important;
}

/* --- Accepted field: compact row --- */
.field-accepted {
  background: #fff;
  border: 1px solid #E8EAED;
  padding: 6px 10px;
}
.field-accepted:hover {
  background: #F8F9FA;
}

.field-row-compact {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
}
.field-label-compact {
  font-size: 12px;
  color: #80868B;
  flex-shrink: 0;
  min-width: 100px;
}
.field-value-compact {
  font-size: 13px;
  color: #202124;
  font-weight: 500;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.field-value-edited {
  color: #1967D2;
}
.undo-btn {
  opacity: 0;
  transition: opacity 0.15s ease;
}
.field-accepted:hover .undo-btn {
  opacity: 0.6;
}
.field-accepted:hover .undo-btn:hover {
  opacity: 1;
}

/* --- Rejected --- */
.field-rejected {
  background: #FFF;
  border: 1px solid #E8EAED;
  padding: 6px 10px;
  opacity: 0.5;
}
.field-row-rejected {
  opacity: 0.7;
}

/* --- Edited --- */
.field-edited {
  background: #F0F7FF;
  border: 1px solid #AECBFA;
  padding: 6px 10px;
}

/* --- Empty --- */
.field-empty {
  background: #FFF;
  border: 1px dashed #DADCE0;
  padding: 6px 10px;
}
.field-row-empty {
  opacity: 0.7;
}

/* --- Edit overlay --- */
.field-editing {
  background: #FFF;
  border: 2px solid #4285F4;
  padding: 8px 10px;
  box-shadow: 0 2px 12px rgba(66, 133, 244, 0.15);
}
.edit-overlay {
  margin-top: 4px;
}
.edit-input {
  font-size: 14px;
}
</style>
