import { computed } from 'vue'
import type { useWorkspaceState } from './useWorkspaceState'

type WorkspaceState = ReturnType<typeof useWorkspaceState>

export function useAnalysisWorkspace(state: WorkspaceState) {
  const analysisPoints = computed(() => state.analysisData.value?.points ?? [])
  const analysisRecordings = computed(
    () => state.analysisData.value?.recordings ?? { count: 0, total_bytes: 0, protected_count: 0 },
  )
  const chartValues = computed(() =>
    analysisPoints.value
      .map((point) => point.subscriber_count)
      .filter((value): value is number => value !== null),
  )
  const chartMinimum = computed(() => Math.min(0, ...chartValues.value))
  const chartMaximum = computed(() => Math.max(1, ...chartValues.value))

  function chartX(index: number): number {
    return analysisPoints.value.length < 2 ? 5 : 5 + (index * 690) / (analysisPoints.value.length - 1)
  }

  function chartY(value: number): number {
    const range = chartMaximum.value - chartMinimum.value || 1
    return 205 - ((value - chartMinimum.value) / range) * 190
  }

  const chartSegments = computed(() => {
    const segments: string[] = []
    let current: string[] = []
    analysisPoints.value.forEach((point, index) => {
      if (point.subscriber_count === null) {
        if (current.length) segments.push(current.join(' '))
        current = []
      } else {
        current.push(`${current.length ? 'L' : 'M'}${chartX(index)},${chartY(point.subscriber_count)}`)
      }
    })
    if (current.length) segments.push(current.join(' '))
    return segments
  })

  return {
    analysisPoints,
    analysisRecordings,
    chartValues,
    chartMinimum,
    chartMaximum,
    chartX,
    chartY,
    chartSegments,
  }
}
