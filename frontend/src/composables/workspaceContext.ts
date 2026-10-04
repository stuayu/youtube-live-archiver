import { inject } from 'vue'
import type { InjectionKey } from 'vue'
import type { useWorkspace } from './useWorkspace'

export type WorkspaceContext = ReturnType<typeof useWorkspace>

export const workspaceKey: InjectionKey<WorkspaceContext> = Symbol('workspace')

export function useWorkspaceContext(): WorkspaceContext {
  const workspace = inject(workspaceKey)
  if (!workspace) {
    throw new Error('Workspace context is unavailable')
  }
  return workspace
}
