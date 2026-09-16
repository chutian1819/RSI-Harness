#!/bin/sh
# Repeatable macOS setup; personal files live outside this checkout.
set -eu
PROJECT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENGINE_SHA=33c4f8dfac4359987f2e814e187de67c332498de
RUNTIME=${RSIH_WRITING_RUNTIME:-"$HOME/.local/share/rsih-writing-runtime"}
ENGINE="$RUNTIME/engine-$ENGINE_SHA"
case "${1:-}" in ''|--check) ;; *) printf 'Usage: %s [--check]\n' "$0"; exit 2;; esac
for tool in git python3 node npm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf '缺少 %s。请先按 docs/GETTING_STARTED.md 安装，再重试。\n' "$tool" >&2
    exit 1
  fi
done
python3 -c 'import sys; assert sys.version_info >= (3,10), "需要 Python 3.10 或以上"'
node -e 'const [a,b]=process.versions.node.split(".").map(Number); if(a<22||(a===22&&b<19)){console.error("需要 Node 22.19 或以上");process.exit(1)}'
printf 'Git、Python、Node 和 npm 检查通过。\n'
if [ "${1:-}" = --check ]; then exit 0; fi
mkdir -p "$RUNTIME"
if [ ! -d "$ENGINE" ]; then
  git clone https://github.com/CosmosMind-ai/RSI-Harness.git "$ENGINE"
fi
if [ "$(git -C "$ENGINE" remote get-url origin)" != 'https://github.com/CosmosMind-ai/RSI-Harness.git' ]; then
  printf '引擎目录的来源不同，未覆盖：%s\n' "$ENGINE" >&2; exit 1
fi
if [ -n "$(git -C "$ENGINE" status --porcelain)" ]; then
  printf '引擎目录有本机修改，未覆盖：%s\n' "$ENGINE" >&2; exit 1
fi
git -C "$ENGINE" checkout --detach "$ENGINE_SHA"
(cd "$ENGINE" && RSIH_INSTALL_DIR="$RUNTIME/bin" RSIH_LIB_DIR="$RUNTIME/lib" ./install.sh --copy)
if [ ! -x "$PROJECT/.venv/bin/python" ]; then python3 -m venv "$PROJECT/.venv"; fi
"$PROJECT/.venv/bin/python" -m pip install -c "$PROJECT/requirements.lock" "$PROJECT"
"$PROJECT/.venv/bin/writing-memory-rsih" setup --rsih "$RUNTIME/bin/rsih" --skip-key
printf '\n安装完成。程序保留了已有密钥和基础 Genome。\n'
printf '下一步在项目目录运行：\n  .venv/bin/writing-memory-rsih setup\n  .venv/bin/writing-memory-rsih web\n'
printf '引擎保存在 %s，请不要删除该运行目录。\n' "$RUNTIME"
