export interface PanelLoader {
  run: () => Promise<void>
  reportsError: boolean
}

/** 取得と反映を 1 つのパネルとしてまとめる。reportsError のパネルだけ失敗を画面に出す。 */
export function panel<T>(
  load: () => Promise<T>,
  apply?: (value: T) => void,
  reportsError = false,
): PanelLoader {
  return {
    run: async () => {
      const value = await load()
      apply?.(value)
    },
    reportsError,
  }
}

/**
 * 各パネルを並行に読み込み、終わったものから反映する。
 * 遅いパネル（チャットなど）を待たずに、要約などの取得済みパネルを表示するため。
 */
export async function loadPanelsIndependently(
  loaders: PanelLoader[],
  onError: (message: string) => void,
): Promise<void> {
  await Promise.all(
    loaders.map(async (loader) => {
      try {
        await loader.run()
      } catch (error) {
        if (loader.reportsError) onError(error instanceof Error ? error.message : '情報を取得できません')
      }
    }),
  )
}
