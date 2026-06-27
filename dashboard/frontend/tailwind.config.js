/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  safelist: [
    'bg-ngsat-green/10', 'text-ngsat-green', 'border-ngsat-green/20',
    'bg-ngsat-green/20',
    'bg-ngsat-red/10', 'text-ngsat-red', 'border-ngsat-red/20',
    'bg-ngsat-red/20',
    'bg-ngsat-yellow/10', 'text-ngsat-yellow', 'border-ngsat-yellow/20',
    'bg-ngsat-yellow/20',
    'bg-ngsat-blue/10', 'text-ngsat-blue', 'border-ngsat-blue/20',
    'bg-ngsat-blue/20',
    'bg-ngsat-purple/10', 'text-ngsat-purple', 'border-ngsat-purple/20',
    'bg-ngsat-purple/20',
  ],
  theme: {
    extend: {
      colors: {
        ngsat: {
          bg: '#0f1117',
          card: '#1a1d27',
          border: '#2a2d3a',
          text: '#e4e6eb',
          muted: '#8b8e98',
          accent: '#3b82f6',
          green: '#22c55e',
          red: '#ef4444',
          yellow: '#eab308',
          purple: '#a855f7',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
    },
  },
  plugins: [],
}
