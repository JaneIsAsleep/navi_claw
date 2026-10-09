#!/bin/sh
set -eu
NAVI_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$NAVI_ROOT"
if [ ! -x "$NAVI_ROOT/.venv/bin/python" ]; then
  printf "%s\n" "请先在项目目录执行 ./setup，准备 Python 和前端依赖。"
  exit 1
fi
export PYTHONPATH="$NAVI_ROOT/backend"
exec "$NAVI_ROOT/.venv/bin/python" -m navi.launcher status "$@"
