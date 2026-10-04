import { computed } from 'vue'
import type { useWorkspaceState } from './useWorkspaceState'

type WorkspaceState = ReturnType<typeof useWorkspaceState>

export function useChannelWorkspace(state: WorkspaceState) {
  const filteredChannels = computed(() =>
    state.channels.value
      .filter(
        (channel) =>
          (!state.channelSearch.value ||
            `${channel.name} ${channel.youtube_channel_id}`
              .toLowerCase()
              .includes(state.channelSearch.value.toLowerCase())) &&
          (state.channelState.value === 'all' || channel.monitor_state === state.channelState.value),
      )
      .sort((a, b) =>
        state.channelSort.value === 'next'
          ? Date.parse(a.next_check_at || '2999') - Date.parse(b.next_check_at || '2999')
          : state.channelSort.value === 'recordings'
            ? b.recording_count - a.recording_count
            : a.name.localeCompare(b.name),
      ),
  )

  return { filteredChannels }
}
