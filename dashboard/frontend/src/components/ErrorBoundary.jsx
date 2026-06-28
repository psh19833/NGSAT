import { Component } from 'react'

export default class ErrorBoundary extends Component {
  state = { hasError: false, error: null }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center min-h-screen bg-ngsat-bg">
          <div className="ngsat-card p-8 max-w-lg text-center">
            <div className="text-4xl mb-4">⚠️</div>
            <h2 className="text-lg font-semibold text-ngsat-text mb-2">오류가 발생했습니다</h2>
            <p className="text-sm text-ngsat-muted mb-4">
              {this.state.error?.message || '알 수 없는 오류'}
            </p>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 text-sm rounded-lg bg-ngsat-accent text-white hover:opacity-90 transition-all"
            >
              페이지 새로고침
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
