import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/resources'
import RuleFormDialog from './RuleFormDialog.vue'

vi.mock('../api/resources', () => ({
  api: {
    channels: vi.fn(),
    storages: vi.fn(),
    qualityPresets: vi.fn(),
    previewRule: vi.fn(),
    saveRule: vi.fn(),
  },
}))

beforeEach(() => {
  vi.mocked(api.channels).mockResolvedValue([])
  vi.mocked(api.storages).mockResolvedValue([])
  vi.mocked(api.qualityPresets).mockResolvedValue([
    { id: 'original', label: '自動', selector: '', description: '自動選択' },
  ])
  vi.mocked(api.previewRule).mockResolvedValue({ matched: 2 })
  vi.mocked(api.saveRule).mockResolvedValue({
    id: 'rule-1',
    name: '配信',
    pattern: '歌枠',
    target: 'both',
    match_mode: 'keyword',
    is_exclusion: false,
    case_sensitive: false,
    channel_id: null,
    enabled: true,
    storage_id: null,
    retention_days: null,
    quality: null,
    edit_detection_mode: null,
    priority: 100,
  })
})

describe('RuleFormDialog', () => {
  it('uses the automatic quality preset by default and supports preview and creation', async () => {
    const wrapper = mount(RuleFormDialog, { props: { open: true } })
    await flushPromises()
    expect(wrapper.get('select').element.value).toBe('false')
    expect(wrapper.findAll('select')[5]!.element.value).toBe('original')
    await wrapper.get('input').setValue('配信')
    await wrapper.findAll('input')[1]!.setValue('歌枠')
    await wrapper.get('button[type="button"]').trigger('click')
    await flushPromises()
    expect(api.previewRule).toHaveBeenCalled()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.saveRule).toHaveBeenCalledWith(
      expect.objectContaining({ name: '配信', pattern: '歌枠', quality: null }),
      undefined,
    )
  })
})
