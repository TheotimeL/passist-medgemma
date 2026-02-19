/**
 * plugins/vuetify.ts
 *
 * Google Material Design theme for PA Form Auto-Fill
 */

import '@mdi/font/css/materialdesignicons.css'
import 'vuetify/styles'

import { createVuetify } from 'vuetify'

export default createVuetify({
  theme: {
    defaultTheme: 'light',
    themes: {
      light: {
        colors: {
          primary: '#4285F4',     // Google Blue
          secondary: '#5F6368',   // Google Gray
          success: '#34A853',     // Google Green
          error: '#EA4335',       // Google Red
          warning: '#FBBC05',     // Google Yellow
          info: '#4285F4',
          surface: '#FFFFFF',
          background: '#F8F9FA',  // Light gray background
          'on-surface': '#202124',
          'on-background': '#202124',
        },
      },
    },
  },
  defaults: {
    VBtn: {
      rounded: 'lg',
      variant: 'flat',
    },
    VCard: {
      rounded: 'lg',
      elevation: 1,
    },
    VChip: {
      rounded: 'lg',
    },
    VTextField: {
      variant: 'outlined',
      density: 'comfortable',
    },
    VSelect: {
      variant: 'outlined',
      density: 'comfortable',
    },
  },
})
