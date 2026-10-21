#!/bin/sh
# OCR-A の MetaPost ソースから中心線（skeleton-round.txt）を書き出す。
# 使い方: ./make_skeleton.sh <ocr-0.3.1 のディレクトリ> <mpost の実行ファイル> <plain.mp のあるディレクトリ>
# （ocr-0.3.1 のうち必要な msk-ocra.mp・msk-ocra10.mp・fntbase.mp は source/ocr-0.3.1 にある）
set -e
if [ $# -ne 3 ]; then
  echo "使い方: $0 <ocr-0.3.1 のディレクトリ> <mpost の実行ファイル> <plain.mp のあるディレクトリ>" >&2
  exit 2
fi
OCR=$(cd "$1" && pwd)
MPOST=$2
MPBASE=$(cd "$3" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
# 文字定義の直前にフックを読み込ませたコピーを作る
python3 -I - "$OCR/msk-ocra.mp" "$WORK/msk-ocra-skel.mp" <<'PY'
import sys
src = open(sys.argv[1], encoding="ascii").read()
marker = '% space added by MS'
assert marker in src
open(sys.argv[2], 'w', encoding="ascii").write(src.replace(marker, 'input skelhooks;\n' + marker, 1))
PY
sed "s/input msk-ocra.mp;/input msk-ocra-skel.mp;/" "$OCR/msk-ocra10.mp" > "$WORK/skel.mp"
if ! (cd "$WORK" && MPINPUTS=".:$HERE:$OCR:$MPBASE:" "$MPOST" -ini -interaction=nonstopmode '\input plain; input skel' >"$WORK/mpost.log" 2>&1); then
  cat "$WORK/mpost.log" >&2
  echo "mpost が失敗しました" >&2
  exit 1
fi
cp "$WORK/skeleton-round.txt" "$HERE/"
echo "wrote $HERE/skeleton-round.txt"
