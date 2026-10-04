import { computed } from 'vue'
import {
  groupUpcoming,
  homeUpcoming as selectHomeUpcoming,
  upcomingStartLabel as labelUpcomingStart,
} from '../lib/upcoming'
import type { Video } from '../api/types'
import type { useWorkspaceState } from './useWorkspaceState'

type WorkspaceState = ReturnType<typeof useWorkspaceState>

export function useUpcomingWorkspace(state: WorkspaceState) {
  const filteredUpcoming = computed(() =>
    state.upcoming.value.filter(
      (video) =>
        (!state.upcomingSearch.value ||
          `${video.title} ${video.channel_name}`
            .toLowerCase()
            .includes(state.upcomingSearch.value.toLowerCase())) &&
        (!state.upcomingFrom.value ||
          (video.scheduled_start_at && video.scheduled_start_at >= state.upcomingFrom.value)) &&
        (!state.upcomingTo.value ||
          (video.scheduled_start_at && video.scheduled_start_at <= `${state.upcomingTo.value}T23:59:59`)),
    ),
  )
  const upcomingGroups = computed(() => groupUpcoming(filteredUpcoming.value))
  const homeUpcoming = computed(() => selectHomeUpcoming(filteredUpcoming.value))

  function upcomingStartLabelFor(video: Video) {
    return labelUpcomingStart(video)
  }

  return { filteredUpcoming, upcomingGroups, homeUpcoming, upcomingStartLabel: upcomingStartLabelFor }
}
