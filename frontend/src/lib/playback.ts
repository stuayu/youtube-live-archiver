export interface MediaInfo {
  container: string
  video_codec: string | null
  audio_codec: string | null
  width: number | null
  height: number | null
}

export interface PlaybackRendition {
  name: 'source' | 'remux' | 'h264'
  url: string
  container: string
  video_codec: string | null
  audio_codec: string | null
  available: boolean
  size: number | null
  status?: string
  progress?: number
}

export interface PlaybackDecision {
  rendition: 'source' | 'remux' | 'h264'
  requiresUserAction: boolean
  reason: string
}

const codecStrings: Record<string, string> = {
  h264: 'avc1.640028',
  avc: 'avc1.640028',
  hevc: 'hvc1.1.6.L120.B0',
  h265: 'hvc1.1.6.L120.B0',
  aac: 'mp4a.40.2',
  mp3: 'mp4a.40.34',
  av1: 'av01.0.05M.08',
  vp9: 'vp09.00.10.08',
  vp8: 'vp8',
  opus: 'opus',
  vorbis: 'vorbis',
  flac: 'flac',
}

type CanPlayType = (type: string) => CanPlayTypeResult | string
type CanPlayTypeResult = 'probably' | 'maybe' | ''

function containerFamily(container: string): 'mp4' | 'webm' | 'unknown' {
  const normalized = container.toLowerCase()
  if (['mp4', 'mov', 'm4v', '3gp', 'isom', 'iso2'].some((name) => normalized.includes(name))) return 'mp4'
  if (['webm', 'matroska'].some((name) => normalized.includes(name)))
    return normalized.includes('webm') ? 'webm' : 'unknown'
  return 'unknown'
}

function codecType(media: MediaInfo, mime: string): string | null {
  const codecs: string[] = []
  for (const codec of [media.video_codec, media.audio_codec]) {
    if (!codec) continue
    const value = codecStrings[codec.toLowerCase()]
    if (!value) return null
    codecs.push(value)
  }
  return codecs.length ? `${mime}; codecs="${codecs.join(', ')}"` : null
}

function supported(type: string | null, canPlayType: CanPlayType): boolean {
  return Boolean(type && canPlayType(type))
}

export function choosePlayback(
  media: MediaInfo | null | undefined,
  renditions: PlaybackRendition[],
  canPlayType: CanPlayType,
): PlaybackDecision {
  const source = renditions.find((item) => item.name === 'source')
  const remux = renditions.find((item) => item.name === 'remux')
  const h264 = renditions.find((item) => item.name === 'h264')
  if (!media) return { rendition: 'source', requiresUserAction: false, reason: 'media-unknown' }
  const unknownCodec = [media.video_codec, media.audio_codec].some(
    (codec) => codec !== null && !codecStrings[codec.toLowerCase()],
  )
  if (unknownCodec) return { rendition: 'source', requiresUserAction: false, reason: 'codec-unknown' }

  const family = containerFamily(media.container)
  const sourceType = family === 'mp4' ? 'video/mp4' : family === 'webm' ? 'video/webm' : null
  if (source && sourceType && supported(codecType(media, sourceType), canPlayType)) {
    return { rendition: 'source', requiresUserAction: false, reason: 'source-supported' }
  }
  if (supported(codecType(media, 'video/mp4'), canPlayType)) {
    return {
      rendition: 'remux',
      requiresUserAction: false,
      reason: remux?.available ? 'remux-ready' : 'remux-required',
    }
  }
  return {
    rendition: 'h264',
    requiresUserAction: true,
    reason: h264?.available ? 'h264-ready' : 'h264-required',
  }
}
