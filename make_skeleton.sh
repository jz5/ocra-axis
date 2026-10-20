#!/bin/sh
# OCR-A の MetaPost ソースから中心線（skeleton-round.txt）を書き出す。
# 使い方: ./make_skeleton.sh <ocr-0.3.1 のディレクトリ> <mpost の実行ファイル> <plain.mp のあるディレクトリ>
set -e
OCR=$(cd "$1" && pwd)
MPOST=$2
MPBASE=$(cd "$3" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
# 文字定義の直前にフックを読み込ませたコピーを作る
python3 -I - "$OCR/msk-ocra.mp" "$WORK/msk-ocra-skel.mp" <<'PY'
import sys
src = open(sys.argv[1]).read()
marker = '% space added by MS'
assert marker in src
open(sys.argv[2], 'w').write(src.replace(marker, 'input skelhooks;\n' + marker, 1))
PY
sed "s/input msk-ocra.mp;/input msk-ocra-skel.mp;/" "$OCR/msk-ocra10.mp" > "$WORK/skel.mp"
(cd "$WORK" && MPINPUTS=".:$HERE:$OCR:$MPBASE:" "$MPOST" -ini -interaction=nonstopmode '\input plain; input skel' >/dev/null)
cp "$WORK/skeleton-round.txt" "$HERE/"
rm -rf "$WORK"
echo "wrote $HERE/skeleton-round.txt"
