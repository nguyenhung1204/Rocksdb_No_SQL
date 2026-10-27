# Hướng dẫn làm đề tài 21 – RocksDB (nhóm 3 người)

Đề tài: *Tìm hiểu RocksDB để quản trị và khai thác CSDL Key-Value cho hệ thống "Xây dựng bộ lưu trữ dữ liệu cục bộ tốc độ cao cho ứng dụng quản lý nhật ký và sự kiện". Xây dựng ứng dụng minh họa.*

---

## 0. Đọc cái này trước

1. **Code trong thư mục này chạy được và đã được test**, nhưng nó chỉ là điểm xuất phát. Giảng viên hỏi bất kỳ dòng nào thì người phụ trách phải giải thích được. Mỗi người **tự đọc, tự chạy, tự sửa** phần của mình ít nhất vài chỗ (đổi tên service, thêm một chức năng nhỏ, đổi tham số rồi đo lại). Phần 7 có gợi ý.
2. **Số liệu benchmark trong tài liệu này là số đo trên máy của mình (sandbox Linux).** Máy của nhóm sẽ ra số khác. Báo cáo phải dùng **số do nhóm tự chạy** và ghi rõ cấu hình máy (CPU, RAM, SSD/HDD, Windows/Linux, phiên bản Python).
3. Kết quả benchmark **không phải RocksDB thắng tất cả**. Phần đó là điểm cộng nếu nhóm phân tích trung thực (xem mục 6).

---

## 1. Hiểu đề tài trong 5 câu

- **RocksDB** là thư viện CSDL **key-value nhúng** (chạy ngay trong tiến trình ứng dụng, không có server), do Meta phát triển từ LevelDB, tối ưu cho ghi nhanh trên SSD.
- Bên trong là **LSM-tree**: ghi vào WAL (đĩa, tuần tự) + memtable (RAM) → khi đầy thì flush thành file **SST** bất biến → nền chạy **compaction** gộp các SST.
- Key được **lưu đã sắp xếp** theo byte, nên **quét theo khoảng key** (range scan) rất nhanh.
- Bài toán log/sự kiện: ghi liên tục, hiếm khi sửa, thường truy vấn theo **thời gian**, và hay **xóa dữ liệu cũ hàng loạt** → khớp với điểm mạnh của LSM-tree + key sắp xếp theo thời gian.
- RocksDB **không có SQL, không có index phụ tự động** → nhóm phải **tự thiết kế key** và **tự xây index** (đây là phần "ăn điểm" của đề tài, đã làm sẵn trong `store.py`).

---

## 2. Phân công 3 thành viên

| Thành viên | Vai trò | Việc chính | Sản phẩm bàn giao |
|---|---|---|---|
| **TV1 – Nhóm trưởng** | Lõi lưu trữ | Thiết kế key schema + 6 column family; viết/giải thích `logstore/store.py`; viết test; tuning option | `logstore/store.py`, `tests/unit/`, chương "Thiết kế hệ thống" + "Cài đặt lõi" |
| **TV2** | Ứng dụng & demo | CLI (`cli.py`), web (`webapp/`, `templates/`), sinh dữ liệu (`logstore/gen.py`); quay/chụp demo; làm slide | `cli.py`, `webapp/`, chương "Ứng dụng minh họa", slide + kịch bản demo |
| **TV3** | Nghiên cứu & đánh giá | Lý thuyết NoSQL/KV/RocksDB/LSM; benchmark (`bench.py`); so sánh với SQLite; tổng hợp báo cáo | `bench.py`, bảng/biểu đồ số liệu, chương "Cơ sở lý thuyết" + "Đánh giá", bản báo cáo hoàn chỉnh |

**Chéo nhau để ai cũng trả lời được:** mỗi người đọc hết `store.py`; TV1 review báo cáo của TV3; TV3 chạy thử toàn bộ hướng dẫn cài đặt của TV2 trên máy sạch; TV2 review phần test của TV1.

### Quy ước làm việc chung
- Dùng **Git** (GitHub/GitLab private). Mỗi người commit phần của mình bằng tên thật. Lịch sử commit là bằng chứng đóng góp.
- Họp ngắn 2 lần/tuần (15–20 phút), ghi biên bản 5 dòng: đã làm / vướng / việc tuần sau.
- Không ai sửa file của người khác mà không báo; xung đột thì tạo branch rồi merge.

---

## 3. Lộ trình 4 tuần (co giãn theo hạn nộp của nhóm)

| Tuần | TV1 | TV2 | TV3 |
|---|---|---|---|
| **1** – Nền tảng | Cài môi trường; chạy test; đọc `store.py`, vẽ sơ đồ key schema | Cài môi trường; chạy CLI + web; hiểu `gen.py` | Đọc tài liệu RocksDB (xem mục 9); viết nháp chương lý thuyết (NoSQL, KV, LSM, WAL, memtable, SST, compaction) |
| **2** – Lõi + ứng dụng | Chỉnh/mở rộng `store.py` (xem mục 7); thêm test cho phần mới | Hoàn thiện CLI + web; tuỳ biến giao diện, dữ liệu mẫu theo đề tài | Viết `bench.py` hoặc chỉnh lại; chạy benchmark lần 1 |
| **3** – Đánh giá | Thử các option (memtable, compression, bloom) và đo ảnh hưởng | Quay demo, chụp màn hình, soạn slide | Chạy benchmark 3 lần, lập bảng + biểu đồ; viết chương đánh giá |
| **4** – Hoàn thiện | Review báo cáo, trả lời câu hỏi bảo vệ | Tập demo, kiểm tra cài đặt trên máy sạch | Gộp báo cáo, định dạng, kiểm tra trích dẫn; cả nhóm tập bảo vệ |

---

## 4. Tải và cài đặt (từng bước)

### 4.1 Cần có
- **Python 3.12, bản 64-bit** (tải ở python.org; khi cài tick "Add python.exe to PATH"). Đây là phiên bản đã kiểm tra cùng Flask 3.1.3 và `rocksdict` 0.3.29 trên Windows. Khi gặp lỗi không có bản thư viện phù hợp, dùng đúng Python 3.12 64-bit.
- **Git** (tuỳ chọn nhưng nên dùng).
- Trình soạn code: VS Code hoặc PyCharm.

### 4.2 Cài đặt

Hướng dẫn clone, chạy lần đầu và nạp tài khoản mẫu nằm trong [README](../../README.md). Windows (PowerShell), tại thư mục chứa `app.py`:
```powershell
cd Rocksdb_No_SQL
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```
Nếu máy không có lệnh `py`, kiểm tra `python --version` là 3.12 rồi dùng `python -m venv .venv`. Các lệnh trên gọi trực tiếp Python của môi trường, không cần kích hoạt `Activate.ps1`.

Linux / macOS / WSL:
```bash
cd Rocksdb_No_SQL
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 4.3 Nếu `pip install rocksdict` lỗi
1. Kiểm tra `.\.venv\Scripts\python.exe --version` là 3.12 và cập nhật pip bằng `.\.venv\Scripts\python.exe -m pip install --upgrade pip`.
2. Kiểm tra đang dùng Python 64-bit và có kết nối Internet. Xem thêm mục xử lý lỗi trong README.
3. Không cố tự biên dịch RocksDB từ mã nguồn trên Windows – rất mất thời gian và không cần thiết cho đề tài.

### 4.4 Công cụ dòng lệnh của RocksDB (tuỳ chọn, để minh họa "quản trị")
Đề tài có chữ "quản trị", nên nếu có WSL/Ubuntu, có thể cài thêm bộ công cụ chính thức:
```bash
sudo apt install rocksdb-tools        # cung cấp lệnh ldb và sst_dump
ldb --db=data/logdb --column_family=idx_level scan --max_keys=5 --hex
sst_dump --file=data/logdb/<file>.sst --command=scan --read_num=5
```
Nếu tên gói hoặc tham số khác trên máy bạn thì xem `ldb --help`. Phần này chỉ để chụp màn hình minh họa, không bắt buộc.

---

## 5. Cách chạy và kiểm tra từng phần

Luôn chạy trong thư mục gốc dự án, nơi có `app.py`. Trong các ví dụ dưới đây, thay `python` bằng `.\.venv\Scripts\python.exe` trên Windows hoặc `.venv/bin/python` trên Linux/macOS nếu chưa kích hoạt môi trường.

| Mục đích | Lệnh | Kết quả mong đợi |
|---|---|---|
| Chạy test | `python -m unittest discover -s tests -v` | Tất cả kiểm thử lõi, web và CLI kết thúc bằng `OK` |
| Sinh 100k log | `python cli.py gen --n 100000` | In số sự kiện/giây |
| Xem thống kê | `python cli.py stats` | Bảng số key, SST, memtable theo 6 column family |
| Đếm theo mức | `python cli.py count` | DEBUG/INFO/WARN/ERROR/FATAL |
| Lỗi 2 giờ qua | `python cli.py query --last 2h --level ERROR` | Danh sách + thời gian truy vấn (ms) |
| Theo nguồn + từ khóa | `python cli.py query --source payment-service --contains cham` | Log của service đó có chữ "cham" |
| Theo trace | `python cli.py query --trace-id <trace-id>` | Các sự kiện của cùng một trace |
| Theo ngày giờ | `python cli.py query --start "2026-10-12 08:00" --end "2026-10-12 09:00"` | Log trong khoảng |
| Ghi 1 log | `python cli.py add ERROR demo-app "Loi thu nghiem"` | In ra id |
| Đọc theo id | `python cli.py get <id>` | JSON của sự kiện |
| Xóa log cũ | `python cli.py purge --older-than 12h --compact` | Số bản ghi xóa, dung lượng trước/sau |
| Backup | `python cli.py backup backups/b1` | Thư mục checkpoint; thư mục đích phải chưa tồn tại |
| Import / export | `python cli.py import data/input.jsonl`; `python cli.py export data/output.jsonl` | Nhập/xuất sự kiện ở định dạng JSONL |
| Restore | `python cli.py restore backups/b1 data/restored-db` | Tạo DB mới từ checkpoint; thư mục đích phải chưa tồn tại |
| Web demo | `python app.py` rồi mở `http://127.0.0.1:5000` | Tạo admin, nạp 4 hồ sơ giả lập và 32 log theo 5 trace nghiệp vụ |
| Seed mẫu ngoại tuyến | `python cli.py --db data/webdb seed-demo --password "mat-khau-tam-demo"` | Cần admin hoạt động; phải dừng web trước khi dùng CLI |
| Benchmark | `python bench.py --n 200000 --write-repeat 3 --repeat 30 --json ketqua.json` | Bảng so sánh, gồm p50/p95/p99 |

Web và REST API yêu cầu đăng nhập; các thao tác ghi cần CSRF token. Khi trình diễn, dùng giao diện web hoặc gửi yêu cầu từ phiên đăng nhập của trình duyệt. CLI là cách gọn hơn để chạy các thao tác quản trị ngoại tuyến.

**Lỗi thường gặp**
- `IO error: lock hold by current process` hoặc `While lock file ... Resource temporarily unavailable`: có tiến trình khác (CLI/web/terminal cũ) đang mở cùng thư mục DB. Tắt nó đi. Đây là đặc tính của CSDL nhúng, nên nhắc trong báo cáo.
- `ModuleNotFoundError: logstore`: đang chạy sai thư mục; phải đứng ở thư mục gốc dự án.
- Web mở được nhưng trống: bấm "Sinh dữ liệu mẫu" hoặc chọn "Tất cả" ở ô khoảng thời gian.

---

## 6. Thiết kế (phần cốt lõi, TV1 phải nắm chắc)

### 6.1 Sáu column family

| Column family | Key | Value | Vai trò |
|---|---|---|---|
| `default` | `event_id` (12 byte) | JSON `{level, source, msg, fields}` | Dữ liệu chính |
| `idx_level` | `level(1B) + event_id` | rỗng | Truy vấn theo mức độ |
| `idx_source` | `source + 0x00 + event_id` | rỗng | Truy vấn theo nguồn |
| `idx_trace` | `trace_id + 0x00 + event_id` | rỗng | Truy vấn theo trace |
| `meta` | `source`, `\x00count:*` | `1`, bộ đếm | Danh sách nguồn và số lượng sự kiện bền vững |
| `users` | tên đăng nhập chuẩn hóa | JSON chứa password hash, role, trạng thái | Tài khoản demo |

### 6.2 event_id = 8 byte timestamp (ms, big-endian) + 4 byte seq
- **Big-endian** để so sánh byte-theo-byte trùng với thứ tự số → quét theo thời gian không cần sort.
- **seq** để hai sự kiện cùng mili-giây không đè nhau. Log đến muộn giữ timestamp gốc và nhận sequence còn trống; với thời điểm mới nhất, sequence được tiếp tục từ key cuối khi mở DB. Vì key sắp xếp theo timestamp, truy vấn vẫn theo thời điểm sự kiện. Có test cho cùng mili-giây, log đến muộn và mở lại DB.
- Dấu `0x00` ngăn cách `source` với `event_id` để source `api` không khớp nhầm `api-gateway` (có test).

### 6.3 Ghi nguyên tử bằng WriteBatch
Mỗi sự kiện ghi vào bản ghi chính và các index theo mức, nguồn, trace. Gói trong một `WriteBatch` thì RocksDB ghi nguyên tử xuyên các column family. Khi sửa hoặc xóa, bản ghi chính và index liên quan cũng thay đổi trong một batch.

### 6.4 Truy vấn
- Khoảng thời gian: `seek(lo)` rồi `next()` đến khi key ≥ `hi`.
- Lọc `level ≥ X`: duyệt index của từng mức ≥ X rồi **gộp theo id** bằng `heapq.merge` (mỗi luồng đã sắp xếp sẵn).
- Lọc nguồn: duyệt `idx_source` theo tiền tố.
- Lọc trace: duyệt `idx_trace` theo tiền tố. Retention xóa trace range theo từng batch, không giữ mọi trace ID trong Python set.
- Phân trang: `cursor` là event ID loại trừ của trang vừa xem; API trả `has_more` và `next_cursor`, giao diện có nút trang trước/sau.
- `--contains` (tìm chuỗi trong nội dung): **không có index**, phải duyệt và lọc → chậm hơn nhiều. Đây là hạn chế thật của KV store, nêu thẳng trong báo cáo (giải pháp: thêm inverted index hoặc dùng công cụ tìm kiếm chuyên dụng).

### 6.5 Retention bằng DeleteRange
Xóa log cũ = ghi range tombstone cho các khoảng key trong bản ghi chính và ba index. Dung lượng chỉ thực sự giảm sau compaction (lệnh `purge --compact`).

### 6.6 Sao lưu bằng Checkpoint
Checkpoint tạo bản sao nhất quán bằng **hard-link** các file SST bất biến → rất nhanh, không chặn ghi. Khôi phục = copy thư mục checkpoint thành thư mục DB mới (có test).

### 6.7 Các option đã chỉnh (và lý do)
LZ4 compression (giảm dung lượng, tốn ít CPU), Bloom filter 10 bit/key (tránh đọc SST không chứa key), block cache 32 MB, memtable 64 MB × 3, 4 job nền. Muốn nói "đã tuning" thì **phải đo trước/sau** (việc của TV1 tuần 3), đừng chỉ liệt kê.

---

## 7. Gợi ý tự mở rộng (để bài của nhóm khác bài mẫu và để hiểu sâu)

Nhóm có thể tự mở rộng thêm sau khi hoàn thành demo:
1. **Đổi bối cảnh dữ liệu** cho sát đề tài: tên service, mẫu thông báo trong `logstore/gen.py`.
2. **TTL tự động**: tác vụ chạy `purge_older_than` theo lịch và ghi nhật ký thao tác quản trị.
3. **Thống kê theo phút** (số ERROR mỗi phút) bằng cách quét range hoặc lưu bộ đếm theo thời gian.
4. **So sánh option**: bật/tắt LZ4, Bloom filter, đổi kích thước memtable rồi đo dung lượng và tốc độ.
5. **Ghi `sync=True`** và đo chi phí độ bền (`python cli.py --sync gen ...`).
6. **Mở rộng thu thập log**: dùng `POST /api/ingest` với Bearer token; ví dụ client có ở `examples/emit_logs.py`.
7. **Tách audit store** nếu đồ án được mở rộng thành hệ thống vận hành thật cần audit không bị rollback theo checkpoint/retention của log.

---

## 8. Kết quả benchmark mẫu (máy của mình, 200.000 sự kiện, 3 lần chạy)

| Phép đo | RocksDB | SQLite | Nhận xét |
|---|---|---|---|
| Ghi theo lô 1000 (sự kiện/giây) | 142–149 nghìn | 163–172 nghìn | SQLite nhỉnh hơn ~15% |
| Ghi từng sự kiện (sự kiện/giây) | 81–94 nghìn | 18–22 nghìn | RocksDB ~4× |
| Truy vấn 10 phút, limit 100 | 0,36–0,62 ms | 0,16–0,25 ms | SQLite nhanh hơn |
| `level ≥ ERROR` trong 10 phút | 0,35–0,41 ms | 0,40–0,45 ms | Ngang nhau |
| Theo nguồn trong 10 phút | 0,63–0,67 ms | 0,17–0,19 ms | SQLite nhanh hơn ~3,5× |
| Đọc 1 bản ghi theo id | 4,4–4,6 µs | 6,4–7,4 µs | RocksDB nhỉnh hơn |
| Dung lượng đĩa | 15,7 MB | 35,9 MB | RocksDB nhỏ hơn ~2,3× (nhờ LZ4) |
| Xóa 1 nửa dữ liệu cũ | 73–74 ms | 233–274 ms | RocksDB ~3,3× |

**Cách giải thích trung thực (rất nên đưa vào báo cáo):**
- RocksDB thắng rõ ở: ghi lẻ liên tục, xóa hàng loạt theo khoảng, dung lượng — đúng với đặc thù log.
- SQLite thắng ở truy vấn khoảng nhỏ/theo nguồn. Giải thích hợp lý nhất (mình chưa profiling để chứng minh, nhóm có thể kiểm chứng bằng `cProfile`): (a) SQLite là C thuần, còn ở đây gọi RocksDB qua binding Python nên mỗi dòng kết quả tốn thêm chi phí giải mã JSON và vòng lặp Python; (b) index theo nguồn của RocksDB phải đọc thêm giá trị bằng `get` cho từng id (đọc ngẫu nhiên), còn SQLite đọc thẳng từ cây B-tree.
- Kết luận đúng: *không có CSDL "nhanh nhất" tuyệt đối*; RocksDB hợp khi **ghi nhiều, ít cập nhật, xóa theo thời gian**; SQLite hợp khi cần **truy vấn linh hoạt bằng SQL**. Nếu cần cả hai thì lưu log vào RocksDB và dùng thêm công cụ tìm kiếm.
- Lưu ý khi so sánh: SQLite chạy `WAL + synchronous=NORMAL`, RocksDB không fsync từng lần ghi (mặc định). Cả hai đều không "bảo đảm không mất dữ liệu khi mất điện" ở chế độ này; có thể đo thêm với `sync=True` / `synchronous=FULL` (kết quả phụ thuộc mạnh vào ổ đĩa).

---

## 9. Tài liệu nên đọc (TV3, và cả nhóm lướt qua)
- Wiki chính thức của RocksDB trên GitHub (`facebook/rocksdb`): các trang *RocksDB Overview*, *Column Families*, *Write Batch*, *Checkpoints*, *DeleteRange*, *Compaction*, *Bloom Filter*, *Basic Operations*.
- Tài liệu của thư viện `rocksdict` (trên PyPI / GitHub) cho các hàm Python đã dùng.
- Bài báo LSM-tree gốc (O'Neil và cộng sự, 1996) và bài báo về LevelDB/RocksDB để trích dẫn.
- Tài liệu SQLite về WAL mode (để so sánh công bằng).

Nhớ **ghi nguồn đầy đủ** trong báo cáo; chỗ nào lấy ý từ tài liệu thì diễn đạt lại bằng lời của nhóm.

---

## 10. Checklist trước khi nộp
- [ ] Cài từ đầu trên **máy sạch** theo đúng hướng dẫn, chạy toàn bộ test và lưu kết quả.
- [ ] Benchmark là **số nhóm tự chạy**, có ghi cấu hình máy, chạy ≥ 3 lần.
- [ ] Báo cáo có sơ đồ: kiến trúc tổng thể, luồng ghi LSM, bố cục key và column family.
- [ ] Có mục **hạn chế và hướng phát triển** (không full-text search, chỉ một tiến trình mở DB, không phân tán...).
- [ ] Mỗi người trả lời được câu hỏi trong `03_CAU_HOI_BAO_VE.md` về phần của mình và đại ý phần của người khác.
- [ ] Tập demo ≥ 2 lần, có phương án dự phòng (video quay sẵn) nếu máy lỗi.
- [ ] Lịch sử Git thể hiện đóng góp của cả 3 người.
