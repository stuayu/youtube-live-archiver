#!/usr/bin/env bash
# frontend Playwright shots を PC / タブレット / スマホの3幅、light / dark で撮影する。
# Usage: bash scripts/shot.sh [latest|review]
# Windows は Git Bash、macOS / Linux は Bash から実行する。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/frontend"
npm run shots -- "${1:-latest}"
