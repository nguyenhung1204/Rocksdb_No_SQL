#!/bin/sh
# Demo công cụ chính thức của RocksDB (ldb, sst_dump) trên một bản sao lưu checkpoint của LogStore.
#
# Chạy trong WSL Ubuntu 24.04 (gói rocksdb-tools 8.9.1 đọc được CSDL do rocksdict/RocksDB 9.x tạo):
#   sudo apt install rocksdb-tools
#   sh scripts/ldb_demo.sh data/backups/backup-YYYYMMDD-HHMMSS-xxxxxxxx
#
# Script chép checkpoint sang thư mục tạm rồi mới mở bằng ldb, vì ldb ghi tệp LOG/LOCK vào thư mục
# CSDL; nhờ vậy bản sao lưu gốc giữ nguyên và không đụng tới CSDL đang chạy trên web.
set -eu
LDB=${LDB:-ldb}
SST_DUMP=${SST_DUMP:-sst_dump}
SRC=${1:?"Cách dùng: sh scripts/ldb_demo.sh <thư mục checkpoint>"}
[ -f "$SRC/CURRENT" ] || { echo "Không phải thư mục checkpoint RocksDB: $SRC"; exit 1; }
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
cp -r "$SRC/." "$WORK/"
OPTS="--db=$WORK --ignore_unknown_options --try_load_options"
hex() { printf '%s' "$1" | od -An -tx1 | tr -d ' \n' | tr 'a-f' 'A-F'; }
TRACE=${TRACE:-DEMO-ORDER-003}

echo "== 1. Liệt kê column family =="
$LDB $OPTS list_column_families

echo; echo "== 2. Bộ đếm trong CF meta (khóa bắt đầu bằng byte 0x00, hiển thị dạng hex) =="
$LDB $OPTS --column_family=meta scan --key_hex | grep '^0x00'

echo; echo "== 3. Chỉ mục theo trace: quét các khóa bắt đầu bằng '$TRACE' + byte 0x00 trong CF idx_trace =="
FROM=0x$(hex "$TRACE")00
TO=0x$(hex "$TRACE")01
$LDB $OPTS --column_family=idx_trace scan --from=$FROM --to=$TO --key_hex

echo; echo "== 4. Đọc bản ghi đầu tiên của trace trong CF default theo mã sự kiện 12 byte =="
FIRST=$($LDB $OPTS --column_family=idx_trace scan --from=$FROM --to=$TO --key_hex --max_keys=1 | cut -d' ' -f1)
ID=0x$(printf '%s' "$FIRST" | tail -c 24)
echo "Mã sự kiện: $ID (8 byte thời điểm ms + 4 byte số thứ tự)"
$LDB $OPTS get "$ID" --key_hex

echo; echo "== 5. Ước lượng số khóa của CF default =="
$LDB $OPTS get_property rocksdb.estimate-num-keys | head -3

echo; echo "== 6. sst_dump: thuộc tính của tệp SST lớn nhất =="
SST=$(ls -S "$WORK"/*.sst | head -1)
$SST_DUMP --file="$SST" --show_properties | grep -E "^(Process|Sst file|  (# data blocks|# entries|raw key size|raw value size|data block size|column family name|comparator name|SST file compression algo|Column family|creation time|DB identity|format version|prefix extractor|filter policy))" | head -20
