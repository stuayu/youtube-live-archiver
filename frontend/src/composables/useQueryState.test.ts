import { beforeEach, describe, expect, it } from 'vitest'
import { readQuery, useQueryState } from './useQueryState'
describe('query state', () => {
  beforeEach(() => {
    history.replaceState(null, '', '/')
  })
  it('restores known tab and preserved filter names', () => {
    const result = readQuery(
      '?tab=videos&channel=12&q=hello&vstatus=ACTIVE&reason=MANUAL&from=a&to=b&min_size=1&max_size=4&storage_id=3&permanent=true&sort=size&direction=asc',
    )
    expect(result.tab).toBe('videos')
    expect(result.values).toEqual({
      channel: '12',
      q: 'hello',
      vstatus: 'ACTIVE',
      reason: 'MANUAL',
      from: 'a',
      to: 'b',
      min_size: '1',
      max_size: '4',
      storage_id: '3',
      permanent: 'true',
      sort: 'size',
      direction: 'asc',
    })
  })
  it('defaults to home and omits defaults from url', () => {
    expect(readQuery('?tab=unknown').tab).toBe('home')
    const state = useQueryState()
    expect(location.search).toBe('')
    state.dispose()
  })
})
