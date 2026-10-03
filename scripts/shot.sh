#!/bin/bash
# GUI を PC / タブレット / スマホ幅で撮る。タブ指定も可能。
#   usage: bash scripts/shot.sh <label> [base_url] [tab...]
# スマホ・タブレット幅は frame.html（iframe で幅を固定）経由で撮る。
# ヘッドレス Chrome は Windows のウィンドウ最小幅に引きずられ、
# --window-size で狭い幅を指定しても実レイアウト幅が広くなるため。
set -u
LABEL="${1:-shot}"
URL="${2:-http://127.0.0.1:8000/}"
if [ "$#" -ge 2 ]; then
  shift 2
elif [ "$#" -ge 1 ]; then
  shift 1
fi
TABS=("$@")
[ ${#TABS[@]} -eq 0 ] && TABS=("channels" "upcoming" "videos" "search" "storages" "analysis" "settings")

CHROME="/c/Program Files/Google/Chrome/Application/chrome.exe"
OUT="C:/prog/youtube-live-archiver/shots"
mkdir -p "$OUT"
TS=$(date +%H%M%S)
BASE="${URL%/}"

shoot() { # name window_size target_url
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --virtual-time-budget=9000 \
    --window-size="$2" --screenshot="$OUT/${LABEL}-$1-${TS}.png" "$3" >/dev/null 2>&1
}

for tab in "${TABS[@]}"; do
  slug="${tab:-home}"
  q=""; [ -n "$tab" ] && q="?tab=$tab"
  shoot "pc-$slug"     1440,1000 "$BASE/$q"
  shoot "tablet-$slug" 840,1180  "$BASE/frame.html?w=820&h=1120${tab:+&tab=$tab}"
  shoot "sp-$slug"     410,880   "$BASE/frame.html?w=390&h=844${tab:+&tab=$tab}"
done
for tab in "${TABS[@]}"; do
  slug="${tab:-home}"
  for width in pc tablet sp; do
    case "$width" in
      pc)    file="$LABEL-pc-$slug-$TS.png" ;;
      tablet) file="$LABEL-tablet-$slug-$TS.png" ;;
      sp)    file="$LABEL-sp-$slug-$TS.png" ;;
    esac
    [ -f "$OUT/$file" ] && printf '%s\n' "$OUT/$file"
  done
done
