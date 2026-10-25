import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const bundle = fileURLToPath(new URL('../node_modules/dplayer/dist/DPlayer.min.js', import.meta.url))
const source = readFileSync(bundle, 'utf8')
const marker = 'Pe={en:De,'
const translations = {
  'danmaku-loading': '弾幕を読み込み中',
  top: '上',
  bottom: '下',
  rolling: '流れる',
  'input-danmaku-enter': 'コメントを入力',
  'about-author': '作者について',
  'dplayer-feedback': 'ご意見',
  'about-dplayer': 'DPlayer について',
  loop: 'ループ',
  speed: '再生速度',
  'opacity-danmaku': '弾幕の不透明度',
  normal: '標準',
  'please-input-danmaku': 'コメントを入力してください',
  'set-danmaku-color': '弾幕の色',
  'set-danmaku-type': '弾幕の種類',
  'show-danmaku': '弾幕を表示',
  'video-failed': '動画を読み込めません',
  'danmaku-failed': 'コメントを読み込めません',
  'danmaku-send-failed': 'コメントを送信できません',
  'switching-quality': '画質を切り替えています',
  'switched-quality': '画質を切り替えました',
  ff: '%s 秒進む',
  rew: '%s 秒戻る',
  'unlimited-danmaku': '弾幕を無制限に表示',
  'send-danmaku': 'コメント送信',
  setting: '設定',
  fullscreen: '全画面',
  'web-fullscreen': 'ウェブ全画面',
  send: '送信',
  screenshot: 'スクリーンショット',
  airplay: 'AirPlay',
  chromecast: 'Chromecast',
  subtitle: '字幕',
  off: 'オフ',
  'show-subs': '字幕を表示',
  'hide-subs': '字幕を隠す',
  volume: '音量',
  live: 'ライブ',
  'video-info': '動画情報',
}
const dictionary = Object.entries(translations)
  .map(([key, value]) => `${JSON.stringify(key)}:${JSON.stringify(value)}`)
  .join(',')
const japanese = `Pe={ja:{${dictionary}},en:De,`

const patched = /Pe=\{ja:\{.*?\},en:De,/s
if (patched.test(source)) {
  writeFileSync(bundle, source.replace(patched, japanese))
} else if (source.includes(marker)) {
  writeFileSync(bundle, source.replace(marker, japanese))
} else {
  throw new Error('Unsupported DPlayer localization bundle')
}
