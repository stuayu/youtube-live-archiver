import { describe, expect, it } from 'vitest'
import { choosePlayback, type MediaInfo, type PlaybackRendition } from './playback'

const mkv: MediaInfo = {
  container: 'matroska',
  video_codec: 'h264',
  audio_codec: 'aac',
  width: 1920,
  height: 1080,
}
const candidates: PlaybackRendition[] = [
  {
    name: 'source',
    url: '/source',
    container: 'matroska',
    video_codec: 'h264',
    audio_codec: 'aac',
    available: true,
    size: 1,
  },
  {
    name: 'remux',
    url: '/remux',
    container: 'mp4',
    video_codec: 'h264',
    audio_codec: 'aac',
    available: false,
    size: null,
  },
  {
    name: 'h264',
    url: '/h264',
    container: 'mp4',
    video_codec: 'h264',
    audio_codec: 'aac',
    available: false,
    size: null,
  },
]
const safariCanPlay = (value: string) =>
  value.includes('video/mp4') && value.includes('avc1') ? 'probably' : ''

describe('choosePlayback', () => {
  it('uses remux for H.264 MKV in WebKit even when Chromium can read MKV', () => {
    const result = choosePlayback(mkv, candidates, (value) =>
      value.includes('video/x-matroska') ? 'probably' : safariCanPlay(value),
    )
    expect(result.rendition).toBe('remux')
    expect(result.requiresUserAction).toBe(false)
  })

  it('plays supported MP4 and WebM source files directly', () => {
    expect(
      choosePlayback(
        { ...mkv, container: 'mp4' },
        candidates.map((item) => (item.name === 'source' ? { ...item, container: 'mp4' } : item)),
        safariCanPlay,
      ).rendition,
    ).toBe('source')
    expect(
      choosePlayback(
        { ...mkv, container: 'webm', video_codec: 'vp9', audio_codec: 'opus' },
        candidates.map((item) => (item.name === 'source' ? { ...item, container: 'webm' } : item)),
        (value) => (value.includes('video/webm') ? 'probably' : ''),
      ).rendition,
    ).toBe('source')
  })

  it('asks before H.264 conversion when MP4 codecs are unsupported', () => {
    const result = choosePlayback({ ...mkv, video_codec: 'av1', audio_codec: 'opus' }, candidates, () => '')
    expect(result).toMatchObject({ rendition: 'h264', requiresUserAction: true })
  })

  it('falls back to source when media information is unavailable', () => {
    expect(choosePlayback(null, candidates, () => '')).toMatchObject({
      rendition: 'source',
      reason: 'media-unknown',
    })
  })

  it('tries the source first when a codec name is unknown', () => {
    expect(choosePlayback({ ...mkv, video_codec: 'future-codec' }, candidates, () => '')).toMatchObject({
      rendition: 'source',
      reason: 'codec-unknown',
    })
  })
})
