import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import type { ChatMessage, ChatPage, Transcript, Video } from '../../api/types'
import PlayerPanels from './PlayerPanels.vue'

const video: Video = {
  id: 'video-1',
  youtube_video_id: 'abcdefghijk',
  channel_id: 'channel-1',
  title: '再生テスト',
  status: 'ACTIVE',
  remote_status: 'PUBLIC',
  retention_expires_at: null,
  protected: false,
  protection_reason: null,
  protected_at: null,
  is_partial: false,
  recovery_attempts: 0,
  next_recovery_at: null,
  recording_finished_at: null,
  file_size: 128,
  recorded_at: null,
  recording_heartbeat_at: null,
  original_duration: null,
  has_file: true,
  live_playback_available: false,
  has_thumbnail: false,
  thumbnail_url: null,
  scheduled_start_at: null,
  user_stopped: false,
  duration_seconds: 30,
  deletion_after: null,
  last_error: null,
  archive_backfill: null,
  recording_start_offset_ms: 0,
  segment_count: 0,
  recovery_exhausted: false,
  skip_reason: null,
  cancel_reason: null,
  missed_live: false,
  schedule_enabled: true,
  live_status: null,
  transcription_status: 'pending',
  transcription_error: null,
  storage_id: null,
  channel_name: 'テストチャンネル',
  storage_name: null,
  media_extension: 'mp4',
}

const transcript: Transcript = { status: 'pending', error: null, total: 0, segments: [] }
const emptyChat: ChatPage = {
  messages: [],
  total: 0,
  available_total: 0,
  chat_enabled: true,
  chat_replay: 'available',
  replay: {
    state: 'unavailable',
    checked_at: null,
    next_attempt_at: null,
    attempts: 0,
    error: null,
    covered_from_ms: null,
    covered_to_ms: null,
  },
  superchat_totals: {},
  unknown_amounts: {},
}

function renderChat(messages: ChatMessage[], following = true) {
  const chat = { ...emptyChat, messages, total: messages.length, available_total: messages.length }
  return mount(PlayerPanels, {
    props: {
      tab: 'chat',
      video,
      chat,
      transcript,
      timeline: [],
      summary: null,
      error: '',
      busy: false,
      videoId: video.id,
      chatPage: 0,
      transcriptPage: 0,
      chatQuery: '',
      chatKind: '',
      includeDeleted: true,
      transcriptQuery: '',
      playbackSeconds: 5,
      following,
    },
  })
}

describe('PlayerPanels chat rendering', () => {
  it('highlights the transcript segment at playback time and lets the user turn sync off', async () => {
    const wrapper = mount(PlayerPanels, {
      props: {
        tab: 'transcript',
        video,
        chat: emptyChat,
        transcript: {
          status: 'completed',
          error: null,
          total: 2,
          segments: [
            { start: 0, end: 2, text: '最初の発言' },
            { start: 2, end: 4, text: '次の発言' },
          ],
        },
        timeline: [],
        summary: null,
        error: '',
        busy: false,
        videoId: video.id,
        chatPage: 0,
        transcriptPage: 0,
        chatQuery: '',
        chatKind: '',
        includeDeleted: false,
        transcriptQuery: '',
        playbackSeconds: 2.5,
        following: true,
        syncTranscript: true,
      },
    })

    expect(wrapper.findAll('.transcript-row.is-current').map((row) => row.text())).toEqual([
      '0:00:02 · 次の発言',
    ])
    await wrapper.setProps({ playbackSeconds: 0.5 })
    expect(wrapper.findAll('.transcript-row.is-current').map((row) => row.text())).toEqual([
      '0:00:00 · 最初の発言',
    ])
    const sync = wrapper.get('.transcript-sync-control input')
    await sync.setValue(false)
    expect(wrapper.emitted('update:syncTranscript')?.slice(-1)[0]).toEqual([false])
    await wrapper.setProps({ syncTranscript: false, playbackSeconds: 3 })
    expect(wrapper.find('.transcript-row.is-current').exists()).toBe(false)

    await wrapper.get('.transcript-row').trigger('click')
    expect(wrapper.emitted('seek')).toEqual([[0]])
  })

  it('scrolls the active transcript line into view while sync is on', async () => {
    const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView')
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    })
    const wrapper = mount(PlayerPanels, {
      props: {
        tab: 'transcript',
        video,
        chat: emptyChat,
        transcript: {
          status: 'completed',
          error: null,
          total: 1,
          segments: [{ start: 0, end: 2, text: '現在の発言' }],
        },
        timeline: [],
        summary: null,
        error: '',
        busy: false,
        videoId: video.id,
        chatPage: 0,
        transcriptPage: 0,
        chatQuery: '',
        chatKind: '',
        includeDeleted: false,
        transcriptQuery: '',
        playbackSeconds: 1,
        following: true,
        syncTranscript: true,
      },
    })

    await nextTick()
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest', behavior: 'smooth' })
    wrapper.unmount()
    if (original) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', original)
    else Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView')
  })

  it('does not reload the first or last transcript page when playback is outside its outer bounds', async () => {
    const renderTranscriptPage = (transcriptPage: number, segmentStart: number, playbackSeconds: number) =>
      mount(PlayerPanels, {
        props: {
          tab: 'transcript',
          video,
          chat: emptyChat,
          transcript: {
            status: 'completed',
            error: null,
            total: 250,
            segments: [{ start: segmentStart, end: segmentStart + 2, text: '発言' }],
          },
          timeline: [],
          summary: null,
          error: '',
          busy: false,
          videoId: video.id,
          chatPage: 0,
          transcriptPage,
          chatQuery: '',
          chatKind: '',
          includeDeleted: false,
          transcriptQuery: '',
          playbackSeconds,
          following: true,
          syncTranscript: true,
        },
      })

    const firstPage = renderTranscriptPage(0, 10, 0)
    const lastPage = renderTranscriptPage(2, 990, 5000)

    expect(firstPage.emitted('sync-transcript')).toBeUndefined()
    expect(lastPage.emitted('sync-transcript')).toBeUndefined()
    firstPage.unmount()
    lastPage.unmount()
  })

  it('requests the transcript page around playback when sync is on and the current page is elsewhere', async () => {
    const wrapper = mount(PlayerPanels, {
      props: {
        tab: 'transcript',
        video,
        chat: emptyChat,
        transcript: {
          status: 'completed',
          error: null,
          total: 250,
          segments: [{ start: 0, end: 2, text: '最初の発言' }],
        },
        timeline: [],
        summary: null,
        error: '',
        busy: false,
        videoId: video.id,
        chatPage: 0,
        transcriptPage: 0,
        chatQuery: '',
        chatKind: '',
        includeDeleted: false,
        transcriptQuery: '',
        playbackSeconds: 864,
        following: true,
        syncTranscript: true,
      },
    })

    expect(wrapper.emitted('sync-transcript')).toEqual([[864]])
    await wrapper.setProps({ syncTranscript: false, playbackSeconds: 1200 })
    expect(wrapper.emitted('sync-transcript')).toEqual([[864]])
  })

  it('offers social search and posting inside the playback timeline', () => {
    const wrapper = mount(PlayerPanels, {
      props: {
        tab: 'timeline',
        video,
        chat: emptyChat,
        transcript,
        timeline: [],
        summary: null,
        error: '',
        busy: false,
        videoId: video.id,
        chatPage: 0,
        transcriptPage: 0,
        chatQuery: '',
        chatKind: '',
        includeDeleted: false,
        transcriptQuery: '',
        playbackSeconds: 5,
        following: true,
      },
    })

    expect(wrapper.findAll('button').some((button) => button.text().includes('SNS'))).toBe(true)
  })

  it('offers capture from the current playback position in the social timeline', async () => {
    const wrapper = mount(PlayerPanels, {
      props: {
        tab: 'timeline',
        video,
        chat: emptyChat,
        transcript,
        timeline: [],
        summary: null,
        error: '',
        busy: false,
        videoId: video.id,
        chatPage: 0,
        transcriptPage: 0,
        chatQuery: '',
        chatKind: '',
        includeDeleted: false,
        transcriptQuery: '',
        playbackSeconds: 5,
        following: true,
      },
    })

    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('SNS'))!
      .trigger('click')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('投稿'))!
      .trigger('click')
    expect(wrapper.findAll('button').some((button) => button.text().includes('この場面を追加'))).toBe(true)
  })

  it('adds a selected frame with its playback time and supports removing it before posting', async () => {
    const wrapper = mount(PlayerPanels, {
      props: {
        tab: 'timeline',
        video,
        chat: emptyChat,
        transcript,
        timeline: [],
        summary: null,
        error: '',
        busy: false,
        videoId: video.id,
        chatPage: 0,
        transcriptPage: 0,
        chatQuery: '',
        chatKind: '',
        includeDeleted: false,
        transcriptQuery: '',
        playbackSeconds: 73,
        following: true,
        captureFrame: async () => ({
          data_base64: 'aW1hZ2U=',
          mime_type: 'image/jpeg',
          width: 1280,
          height: 720,
          playback_seconds: 73,
        }),
      },
    })

    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('SNS'))!
      .trigger('click')
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '投稿')!
      .trigger('click')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('この場面を追加'))!
      .trigger('click')
    expect(wrapper.text()).toContain('1/4 枚')
    expect(wrapper.text()).toContain('1:13')
    expect(wrapper.text()).toContain('代替テキスト')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('この画像を削除'))!
      .trigger('click')
    expect(wrapper.text()).toContain('0/4 枚')
  })

  it('limits the capture selection to four images', async () => {
    const wrapper = mount(PlayerPanels, {
      props: {
        tab: 'timeline',
        video,
        chat: emptyChat,
        transcript,
        timeline: [],
        summary: null,
        error: '',
        busy: false,
        videoId: video.id,
        chatPage: 0,
        transcriptPage: 0,
        chatQuery: '',
        chatKind: '',
        includeDeleted: false,
        transcriptQuery: '',
        playbackSeconds: 5,
        following: true,
        captureFrame: async () => ({
          data_base64: 'aW1hZ2U=',
          mime_type: 'image/jpeg',
          width: 320,
          height: 180,
          playback_seconds: 5,
        }),
      },
    })
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'SNS')!
      .trigger('click')
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '投稿')!
      .trigger('click')

    for (let index = 0; index < 4; index += 1) {
      await wrapper
        .findAll('button')
        .find((button) => button.text().includes('この場面を追加'))!
        .trigger('click')
    }
    expect(wrapper.text()).toContain('4/4 枚')
    expect(
      (
        wrapper.findAll('button').find((button) => button.text().includes('この場面を追加'))!
          .element as HTMLButtonElement
      ).disabled,
    ).toBe(true)
  })

  it('explains missing replay, shows covered range, disables retrieval while queued, and seeks to coverage', async () => {
    const wrapper = mount(PlayerPanels, {
      props: {
        tab: 'chat',
        video,
        chat: {
          ...emptyChat,
          replay: {
            state: 'queued',
            checked_at: null,
            next_attempt_at: null,
            attempts: 0,
            error: null,
            covered_from_ms: 31300000,
            covered_to_ms: 35750000,
          },
        },
        transcript,
        timeline: [],
        summary: null,
        error: '',
        busy: false,
        videoId: video.id,
        chatPage: 0,
        transcriptPage: 0,
        chatQuery: '',
        chatKind: '',
        includeDeleted: false,
        transcriptQuery: '',
        playbackSeconds: 0,
        following: true,
      },
    })
    expect(wrapper.text()).toContain('この時刻のコメントは未取得です')
    expect(wrapper.text()).toContain('8:41:40 〜 9:55:50')
    expect(wrapper.get('.replay-empty button').text()).toBe('取得済みの位置へ移動')
    const buttons = wrapper.findAll('.replay-empty button')
    expect((buttons[1]!.element as HTMLButtonElement).disabled).toBe(true)
    expect(buttons[1]!.text()).toBe('取得中…')
    await buttons[0]!.trigger('click')
    expect(wrapper.emitted('seekCovered')).toEqual([[31300]])
  })
  it('renders text, standard emoji, cached emotes, and falls back to the shortcode on image error', async () => {
    const wrapper = renderChat([
      {
        id: 'message-1',
        offset_ms: 1000,
        time_seconds: 1,
        author_name: 'viewer',
        message_text: 'Hello 💚 :_flower:',
        is_deleted: false,
        message_type: 'normal',
        runs: [
          { type: 'text', text: 'Hello ' },
          { type: 'emoji', text: '💚' },
          { type: 'emote', id: 'emote-1', shortcut: ':_flower:', url: '/api/chat-assets/emote-1' },
        ],
        author: {
          name: 'viewer',
          role: 'moderator',
          badges: [{ type: 'member', label: 'Member badge', url: '/api/chat-assets/member' }],
        },
      },
    ])
    expect(wrapper.get('.chat-message').classes()).toContain('normal')
    expect(wrapper.get('.author-moderator').text()).toBe('viewer')
    expect(wrapper.get('.author-badges img').attributes('src')).toBe('/api/chat-assets/member')
    expect(wrapper.get('.chat-body').text()).toContain('💚')
    expect(wrapper.get('.chat-emote').attributes('src')).toBe('/api/chat-assets/emote-1')
    await wrapper.get('.chat-emote').trigger('error')
    expect(wrapper.get('.chat-body').text()).toContain(':_flower:')
    expect(wrapper.find('.chat-emote').exists()).toBe(false)
  })

  it('uses amount cards and localized membership headings and marks deleted messages', () => {
    const wrapper = renderChat([
      {
        id: 'paid',
        offset_ms: 0,
        author_name: 'supporter',
        message_text: 'ありがとう',
        is_deleted: false,
        message_type: 'superchat',
        amount_display: '¥1,000',
        color: '#ffb300',
        runs: [{ type: 'text', text: 'ありがとう' }],
        author: { name: 'supporter', role: 'owner', badges: [] },
      },
      {
        id: 'member',
        offset_ms: 1500,
        author_name: 'member',
        message_text: '',
        is_deleted: true,
        message_type: 'membership',
        header_text: 'Member for 45 months · Gold',
        runs: [],
        author: { name: 'member', role: 'member', badges: [] },
      },
    ])
    expect(wrapper.get('.chat-message.superchat').attributes('style')).toContain('#ffb300')
    expect(wrapper.get('.paid-header').text()).toContain('¥1,000')
    expect(wrapper.get('.chat-message.membership').text()).toContain('メンバー 45 か月 · Gold')
    expect(wrapper.get('.chat-message.membership').classes()).toContain('deleted')
    expect(wrapper.get('.deleted-label').text()).toBe('削除済み')
  })

  it('seeks from timestamps and offers return-to-current-position after manual scroll', async () => {
    const wrapper = renderChat(
      [
        {
          id: 'message-1',
          offset_ms: 7500,
          time_seconds: 7.5,
          author_name: 'viewer',
          message_text: 'Hello',
          is_deleted: false,
          message_type: 'normal',
        },
      ],
      false,
    )
    expect(wrapper.get('.return-to-chat').text()).toBe('現在位置に戻る')
    await wrapper.get('.chat-time').trigger('click')
    expect(wrapper.emitted('seek')).toEqual([[7.5]])
    Object.defineProperties(wrapper.get('.chat-list').element, {
      scrollTop: { value: 0, configurable: true },
      clientHeight: { value: 100, configurable: true },
      scrollHeight: { value: 200, configurable: true },
    })
    await wrapper.get('.chat-list').trigger('scroll')
    expect(wrapper.emitted('chatScrolled')).toBeTruthy()
    await wrapper.get('.return-to-chat').trigger('click')
    expect(wrapper.emitted('followChat')).toBeTruthy()
  })

  it('labels superchat totals and formats currency symbols', async () => {
    const wrapper = renderChat([])
    await wrapper.setProps({
      chat: { ...emptyChat, superchat_totals: { JPY: 4000, USD: 5 } },
    })
    expect(wrapper.get('.superchat-totals').text()).toContain('スーパーチャット合計')
    expect(wrapper.get('.superchat-totals').text()).toContain('¥4,000')
    expect(wrapper.get('.superchat-totals').text()).toContain('$5.00')
  })
})
