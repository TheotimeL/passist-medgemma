<template>
  <div class="fhir-summary">
    <!-- Demographics -->
    <div class="fhir-section">
      <div class="section-title">
        <v-icon size="14" color="primary" class="mr-1">mdi-account</v-icon>
        Patient
      </div>
      <table class="info-table">
        <tr><td class="info-label">Name</td><td class="info-value font-weight-medium">{{ data.name }}</td></tr>
        <tr><td class="info-label">DOB</td><td class="info-value">{{ data.dob }}</td></tr>
        <tr><td class="info-label">Gender</td><td class="info-value text-capitalize">{{ data.gender }}</td></tr>
        <tr><td class="info-label">Phone</td><td class="info-value">{{ data.phone }}</td></tr>
        <tr><td class="info-label">Member ID</td><td class="info-value mono">{{ data.member_id }}</td></tr>
        <tr><td class="info-label">Address</td><td class="info-value">{{ data.address }}</td></tr>
      </table>
    </div>

    <!-- Condition -->
    <div class="fhir-section">
      <div class="section-title">
        <v-icon size="14" color="error" class="mr-1">mdi-stethoscope</v-icon>
        Condition
      </div>
      <table class="info-table">
        <tr><td class="info-label">Diagnosis</td><td class="info-value font-weight-medium">{{ data.condition_display }}</td></tr>
        <tr><td class="info-label">SNOMED</td><td class="info-value mono">{{ data.condition_snomed }}</td></tr>
        <tr><td class="info-label">Onset</td><td class="info-value">{{ data.condition_onset }}</td></tr>
      </table>
    </div>

    <!-- Provider -->
    <div class="fhir-section">
      <div class="section-title">
        <v-icon size="14" color="success" class="mr-1">mdi-hospital-building</v-icon>
        Provider
      </div>
      <table class="info-table">
        <tr><td class="info-label">Provider</td><td class="info-value font-weight-medium">{{ data.provider_name }}</td></tr>
        <tr><td class="info-label">NPI</td><td class="info-value mono">{{ data.provider_npi }}</td></tr>
        <tr><td class="info-label">Facility</td><td class="info-value">{{ data.facility_name }}</td></tr>
      </table>
    </div>

    <!-- Medications -->
    <div class="fhir-section">
      <div class="section-title">
        <v-icon size="14" color="warning" class="mr-1">mdi-pill</v-icon>
        Medications ({{ data.medications.length }})
      </div>
      <div class="d-flex flex-wrap ga-1 mt-1">
        <v-chip
          v-for="med in data.medications"
          :key="med.name"
          size="small"
          variant="tonal"
          :color="med.status === 'active' ? 'success' : 'grey'"
        >
          {{ med.name }}
        </v-chip>
      </div>
      <p v-if="data.medications.length === 0" class="text-caption text-disabled mt-1">
        No medications recorded
      </p>
    </div>

    <!-- Coverage -->
    <div class="fhir-section">
      <div class="section-title">
        <v-icon size="14" color="info" class="mr-1">mdi-shield-check</v-icon>
        Coverage
      </div>
      <table class="info-table">
        <tr><td class="info-label">Insurer</td><td class="info-value font-weight-medium">{{ data.insurer }}</td></tr>
        <tr><td class="info-label">Type</td><td class="info-value">{{ data.coverage_type }}</td></tr>
        <tr><td class="info-label">Last Visit</td><td class="info-value">{{ data.latest_encounter_date }}</td></tr>
      </table>
    </div>
  </div>
</template>

<script lang="ts" setup>
import type { PatientFhir } from '@/stores/patient'

defineProps<{
  data: PatientFhir
}>()
</script>

<style scoped>
.fhir-summary {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.fhir-section {
  padding: 8px 10px;
  border-radius: 6px;
  background: #FAFAFA;
  border: 1px solid #F1F3F4;
}
.section-title {
  display: flex;
  align-items: center;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #80868B;
  margin-bottom: 4px;
}
.info-table {
  width: 100%;
  border-collapse: collapse;
}
.info-table td {
  padding: 1px 0;
  vertical-align: top;
}
.info-label {
  font-size: 12px;
  color: #80868B;
  width: 80px;
  white-space: nowrap;
}
.info-value {
  font-size: 13px;
  color: #202124;
}
.mono {
  font-family: 'Roboto Mono', monospace;
  font-size: 12px;
}
</style>
