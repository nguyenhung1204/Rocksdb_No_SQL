# LogStore RocksDB

Đồ án NoSQL, đề tài 21: xây dựng bộ lưu trữ cục bộ cho nhật ký và sự kiện bằng RocksDB, kèm ứng dụng minh họa.

LogStore lưu sự kiện trong RocksDB, tự duy trì các index theo mức độ, nguồn và `trace_id`. Ứng dụng web chạy cục bộ, có tài khoản và phân quyền; hỗ trợ thêm, tìm, sửa, xóa, nhập/xuất JSONL, retention và backup/restore bằng checkpoint.

## Chạy nhanh trên Windows

Đã kiểm tra với **Python 3.12 64-bit**. Cần Internet để tải mã nguồn và cài thư viện lần đầu; sau đó demo chạy cục bộ. Không cần cài một RocksDB server riêng: thư viện `rocksdict` cung cấp engine RocksDB nhúng, lưu dữ liệu trong thư mục trên máy.

### 1. Lấy mã nguồn

Nếu đã có Git, mở PowerShell trong thư mục muốn lưu dự án:

```powershell
git clone https://github.com/nguyenhung1204/Rocksdb_No_SQL.git
cd Rocksdb_No_SQL
```

Nếu chưa có Git, mở [repository trên GitHub](https://github.com/nguyenhung1204/Rocksdb_No_SQL), chọn **Code → Download ZIP**, giải nén rồi mở PowerShell tại thư mục có `app.py` và `requirements.txt` (thường là `Rocksdb_No_SQL-main`).

### 2. Tạo môi trường và cài thư viện

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Nếu máy không có lệnh `py` nhưng đã cài Python 3.12 64-bit, dùng `python --version` để kiểm tra rồi thay lệnh đầu bằng `python -m venv .venv`. Các lệnh còn lại giữ nguyên. Gọi trực tiếp Python của `.venv` nên không cần kích hoạt môi trường hay đổi Execution Policy của PowerShell.

### 3. Chạy web và tạo dữ liệu mẫu

```powershell
.\.venv\Scripts\python.exe app.py
```

Mở **[http://127.0.0.1:5000](http://127.0.0.1:5000)**. Giữ cửa sổ PowerShell này mở khi sử dụng.

1. Lần chạy đầu chuyển đến `/setup`: tự đặt tên admin và mật khẩu **tối thiểu 10 ký tự**, rồi đăng nhập.
2. Vào **Quản lý người dùng** (`/users`), nhập mật khẩu tạm riêng cho các tài khoản mẫu và bấm **Nạp dữ liệu người dùng và nghiệp vụ mẫu**.
3. Trên DB mới, thao tác này thêm **4 hồ sơ người dùng, 32 sự kiện thuộc 5 trace**. Quay về dashboard để tra cứu `DEMO-ORDER-001` hoặc `DEMO-ORDER-003`.

Repository chứa mã nguồn; không kèm DB đã tạo sẵn hoặc mật khẩu đăng nhập. App tự tạo các column family và thư mục cần thiết khi mở DB, không phải chạy migration hay kết nối máy chủ CSDL từ xa.

## Dừng, chạy lại và giữ dữ liệu

- Dừng server bằng **Ctrl+C** trong PowerShell.
- Lần sau mở PowerShell tại thư mục dự án và chạy lại `.\.venv\Scripts\python.exe app.py`; không cần tạo lại `.venv`, cài lại thư viện hoặc nạp demo.
- Mặc định DB ở `data/webdb`, checkpoint ở `data/backups`; tài khoản và sự kiện được giữ trên ổ đĩa qua các lần chạy. Giữ thư mục `data/` nếu muốn giữ dữ liệu.
- Để bắt đầu lại từ DB trắng, dừng app rồi **đổi tên** thư mục `data` thành một tên chưa tồn tại, ví dụ `data-saved`. Chạy app lại để tạo `data` mới. Nếu từng đặt `LOGSTORE_PATH`, hãy kiểm tra thư mục DB đang dùng trước khi thực hiện.

Ứng dụng chạy bằng Waitress, tự tạo khóa phiên mới mỗi lần chạy nên có thể cần đăng nhập lại sau khi khởi động. Nếu muốn giữ phiên, đặt `LOGSTORE_SECRET_KEY` bằng chuỗi bí mật ngẫu nhiên của riêng nhóm và dùng lại cùng giá trị trong các lần chạy; không đưa giá trị này lên GitHub. App mặc định chỉ lắng nghe tại `127.0.0.1`; có thể đổi `LOGSTORE_HOST`, `LOGSTORE_PORT` và `LOGSTORE_THREADS` nếu cần.

## Chạy bằng Docker Compose

Đây là cách chạy đóng gói thêm bên cạnh hướng dẫn Python ở trên. Cài Docker Desktop, mở PowerShell tại thư mục dự án, rồi tạo tệp cấu hình riêng:

```powershell
Copy-Item .env.example .env
py -c "import secrets; print(secrets.token_urlsafe(48))"
```

Dán chuỗi vừa tạo vào `LOGSTORE_SECRET_KEY` trong `.env`. Nếu dùng `/api/ingest`, tạo thêm một chuỗi ngẫu nhiên khác và đặt vào `LOGSTORE_INGEST_TOKEN`; nếu để trống, API ingest bị tắt. Không commit `.env` hoặc chia sẻ các giá trị này.

```powershell
docker compose up --build -d
docker compose ps
docker compose logs -f logstore
```

Mở **[http://127.0.0.1:5000](http://127.0.0.1:5000)** rồi tạo tài khoản admin như cách chạy local. Docker health check dùng `/health/ready`; `/health/live` chỉ xác nhận tiến trình còn phản hồi. DB và checkpoint nằm trong volume `logstore-data`, nên vẫn còn sau khi thay container. `docker compose down` dừng app và giữ volume; chỉ dùng `docker compose down -v` khi muốn xóa cả dữ liệu. Cổng chỉ được công bố trên loopback máy host. Nếu tự đổi bind host để cho máy khác truy cập, cần tự cấu hình TLS, firewall và secret phù hợp trước khi mở mạng.

Mẫu Compose đặt `LOGSTORE_COOKIE_SECURE=false` vì nó chỉ dùng HTTP trên loopback. Khi đặt sau reverse proxy có HTTPS, hãy bật `LOGSTORE_COOKIE_SECURE=true` trước khi cho người dùng đăng nhập.

## Lỗi thường gặp khi cài/chạy

| Hiện tượng | Cách xử lý |
|---|---|
| Không tìm thấy `py` hoặc `python` | Cài Python 3.12 **64-bit**, bật tùy chọn thêm Python vào PATH rồi mở lại PowerShell. Nếu chỉ thiếu `py`, dùng `python -m venv .venv` sau khi kiểm tra phiên bản. |
| `ModuleNotFoundError: flask` / `rocksdict` | Chạy lại `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`, sau đó dùng đúng `.\.venv\Scripts\python.exe app.py`. |
| Không có wheel tương thích / lỗi build `rocksdict` | Kiểm tra đang dùng Python 3.12 64-bit và nâng cấp pip bằng lệnh ở trên. Nếu `.venv` được tạo từ Python khác, đổi tên môi trường cũ rồi tạo lại bằng `py -3.12 -m venv .venv`. Cài thư viện lần đầu cần Internet. |
| Lỗi `LOCK`, DB đang bị tiến trình khác mở | Dừng cửa sổ app/CLI còn chạy trên cùng thư mục DB rồi thử lại. Không xóa file `LOCK` để ép mở DB. |
| Cổng 5000 đang được sử dụng | Nếu app đã chạy, mở địa chỉ ở trên. Nếu là chương trình khác, dừng đúng chương trình đó rồi chạy lại; có thể xem tiến trình bằng `Get-NetTCPConnection -LocalPort 5000 -State Listen`. |
| Chạy từ sai thư mục / không tìm thấy `app.py` | Mở PowerShell tại thư mục chứa `app.py`, `cli.py`, `requirements.txt`. Luôn chạy tại đây để các đường dẫn dữ liệu tương đối trỏ đúng chỗ. |

## Cấu trúc dự án

```text
Rocksdb_No_SQL/
├── logstore/                  # Lõi RocksDB, key schema, index, truy vấn, retention
│   ├── store.py
│   ├── gen.py                  # Sinh nhật ký tổng hợp
│   └── demo.py                 # Hồ sơ giả lập và luồng nghiệp vụ liên kết
├── webapp/                    # Flask app factory, xác thực, REST API
│   ├── __init__.py
│   ├── auth.py
│   └── api.py
├── templates/                  # Giao diện đăng nhập, dashboard, quản lý người dùng
├── tests/
│   ├── unit/                   # Kiểm thử LogStore
│   └── integration/            # Kiểm thử Flask và CLI
├── docs/
│   ├── handbook/               # Cách phối hợp và chạy demo
│   ├── report-preparation/     # Dàn ý báo cáo bám rubric
│   ├── defense/                # Câu hỏi bảo vệ
│   └── superpowers/             # Đặc tả và kế hoạch hoàn thiện demo
├── app.py                      # Chạy ứng dụng web
├── cli.py                      # Công cụ dòng lệnh
├── bench.py                    # So sánh benchmark RocksDB và SQLite
├── examples/
│   └── emit_logs.py            # Ví dụ ứng dụng khác gửi log qua Bearer token
├── Dockerfile                  # Image chạy Waitress dưới user không phải root
├── docker-compose.yml          # Chạy local kèm volume dữ liệu và health check
├── .env.example                # Mẫu cấu hình; tự tạo secret trước khi chạy
└── requirements.txt
```

Thư mục `data/` chứa dữ liệu phát sinh khi chạy, không chứa mã nguồn. `.gitignore` bỏ qua `data/`, `backups/`, `.venv/` và Python cache. Không đưa DB, checkpoint hoặc mật khẩu thật lên repository.

## Kịch bản trình diễn

1. Tạo admin ở `/setup` rồi đăng nhập.
2. Trong “Quản lý người dùng”, chọn mật khẩu tạm tối thiểu 10 ký tự và bấm “Nạp dữ liệu người dùng và nghiệp vụ mẫu”. Hệ thống thêm 4 nhân sự giả lập (operator/viewer) cùng 32 sự kiện thuộc 5 trace liên kết; có đơn hàng thành công, thanh toán thử lại, giao dịch bị từ chối, thao tác vận hành và checkpoint.
3. Sinh thêm khoảng 5.000 log tổng hợp nếu cần; bảng thống kê hiển thị bản ghi, dung lượng và số key theo column family.
4. Tra cứu `DEMO-ORDER-001` theo trace ID để thấy một đơn hàng đi qua nhiều dịch vụ; thử `DEMO-ORDER-003` để xem xử lý lỗi và hoàn kho.
5. Tra cứu theo thời gian, mức, nguồn hoặc từ khóa; tạo/sửa/xóa sự kiện thử.
6. Vào “Quản lý người dùng”, xem hồ sơ/vai trò rồi đăng nhập bằng một tài khoản mẫu để kiểm tra quyền chỉ đọc hoặc thao tác.
7. Xuất JSONL, nhập lại tệp đó, tạo checkpoint, thêm sự kiện rồi restore để xem trạng thái quay về snapshot. Nhập lại sẽ tạo sự kiện mới với ID mới; restore cũng khôi phục tài khoản/quyền về thời điểm checkpoint.

Các thao tác xóa và khôi phục cần xác nhận trong giao diện. Hãy dùng dữ liệu thử, không dùng DB muốn giữ.

## Đối chiếu thang điểm ở giai đoạn demo

| Nhóm yêu cầu | Trạng thái trong bản demo |
|---|---|
| Tạo/populate DB, truy vấn cơ bản và nâng cao | Có CLI, REST API, index theo mức/nguồn/trace và bộ lọc thời gian/nội dung |
| Import/export, backup/restore | Có trên web và CLI; có kiểm thử JSONL và checkpoint |
| GUI, kết nối RocksDB với ứng dụng, CRUD và quản trị người dùng | Có dashboard Flask, đăng nhập/phân quyền và các thao tác dữ liệu |
| Lý thuyết, nghiên cứu công cụ, benchmark chính thức, báo cáo/slide và minh chứng đóng góp | Chưa hoàn thành trong lượt này; dàn ý báo cáo đã sửa theo yêu cầu 50–60 trang. Cần nhóm bổ sung nghiên cứu có trích dẫn, số đo trên máy nhóm, ảnh demo, phân công và sản phẩm Word/PPT theo yêu cầu cô |

Giao diện web đáp ứng phần demo GUI trong ứng dụng. Nếu cô yêu cầu một công cụ GUI RocksDB độc lập ngoài ứng dụng, nhóm cần xác nhận cách tính điểm với giảng viên; RocksDB là engine nhúng nên không kèm một giao diện quản trị desktop như một số hệ DB khác.

## Tài khoản và vai trò

| Vai trò | Quyền |
|---|---|
| `admin` | Tra cứu, sửa dữ liệu, quản lý người dùng, tạo và khôi phục backup |
| `operator` | Ghi/sửa/xóa sự kiện, nhập/xuất, retention và tạo backup |
| `viewer` | Chỉ tra cứu sự kiện và thống kê |

Admin đầu tiên được tạo một lần khi DB chưa có tài khoản. API ghi yêu cầu phiên đăng nhập và CSRF token. Người dùng bị khóa không thể đăng nhập.

Bốn tài khoản mẫu có tên, email `.test`, phòng ban và quyền `operator`/`viewer`; tất cả dùng mật khẩu tạm do admin nhập lúc nạp. Dữ liệu nhận diện đều giả, không lấy từ người thật. Nạp lại an toàn: không ghi đè tài khoản đang có và không tạo trùng các trace mẫu. Chỉ nạp sau khi đã tạo admin.

| Tài khoản | Nhân sự giả lập | Bộ phận | Vai trò |
|---|---|---|---|
| `lan.nguyen` | Nguyễn Thị Lan | SRE · Vận hành | `operator` |
| `an.le` | Lê Hoàng An | Hỗ trợ ứng dụng | `operator` |
| `minh.tran` | Trần Minh Quân | Giám sát hệ thống | `viewer` |
| `thao.pham` | Phạm Thu Thảo | Kiểm toán nội bộ | `viewer` |

Nếu seed qua CLI thay vì giao diện, dừng app trước vì RocksDB khóa thư mục DB theo tiến trình. Tài khoản demo chỉ phục vụ chạy cục bộ trong lớp học.

## CLI

**Dừng web app trước** khi chạy CLI trên cùng DB. Các tùy chọn toàn cục như `--db` đặt trước tên lệnh. Ví dụ dưới đây dùng `data/webdb`, cùng DB mặc định của web; từng lệnh là một thao tác độc lập:

```powershell
.\.venv\Scripts\python.exe cli.py --db data\webdb gen --n 5000 --hours 24
.\.venv\Scripts\python.exe cli.py --db data\webdb stats
.\.venv\Scripts\python.exe cli.py --db data\webdb query --last 2h --level ERROR
.\.venv\Scripts\python.exe cli.py --db data\webdb query --trace-id DEMO-ORDER-001
.\.venv\Scripts\python.exe cli.py --db data\webdb query --source payment-service --contains timeout
.\.venv\Scripts\python.exe cli.py --db data\webdb query --source payment-service --json
.\.venv\Scripts\python.exe cli.py --db data\webdb add ERROR demo-app "Loi thu nghiem"
.\.venv\Scripts\python.exe cli.py --db data\webdb count
.\.venv\Scripts\python.exe cli.py --db data\webdb purge --older-than 12h --compact
.\.venv\Scripts\python.exe cli.py --db data\webdb backup data\backups\checkpoint-1
.\.venv\Scripts\python.exe cli.py --db data\webdb export data\events.jsonl
.\.venv\Scripts\python.exe cli.py --db data\webdb import data\events.jsonl
.\.venv\Scripts\python.exe cli.py restore data\backups\checkpoint-1 data\restored-db
```

Restore tạo thư mục DB mới; thư mục đích không được tồn tại. JSONL lưu `level`, `source`, `msg`, `fields` và timestamp. Khi nhập, RocksDB cấp ID mới cho mỗi sự kiện.

CLI cũng có `seed-demo --password`: tự nhập mật khẩu tạm riêng thay vì lưu sẵn trong mã nguồn. Hãy tạo admin qua `/setup` **trước khi** seed bằng CLI, rồi dừng web app; `/setup` chỉ dùng được khi DB chưa có tài khoản. Cách nạp trên `/users` ở phần chạy nhanh tránh nhầm thứ tự này.

## Khôi phục DB và chạy lại sau restore

Restore trên web sao checkpoint sang thư mục mới `data/restores/restore-...`, chuyển ứng dụng sang DB đó và ghi lại đường dẫn đang hoạt động trong `data/.logstore-state.json`. Khi app khởi động lại, nó tự mở DB đã restore cùng thư mục backup tương ứng. Nếu đã cấu hình `LOGSTORE_PATH` hoặc `LOGSTORE_BACKUP_PATH`, giá trị cấu hình tường minh được ưu tiên hơn manifest; hãy bỏ cấu hình đó hoặc cập nhật nó tới đường dẫn restore trước khi khởi động lại để tiếp tục dùng DB vừa khôi phục. Muốn chuyển hẳn về DB mặc định, dừng app và xóa `data/.logstore-state.json`; đừng xóa thư mục DB nếu cần giữ dữ liệu.

## Gửi log từ một ứng dụng khác

`POST /api/ingest` nhận một sự kiện hoặc một danh sách JSON tối đa 100.000 sự kiện. Gửi `Authorization: Bearer <token>`. Endpoint bị tắt nếu chưa đặt `LOGSTORE_INGEST_TOKEN`; nó không cần phiên trình duyệt/CSRF nhưng vẫn kiểm tra Bearer token và giới hạn request 10 MB.

Mở PowerShell thứ nhất, tạo token tạm và chạy app:

```powershell
$env:LOGSTORE_INGEST_TOKEN = [guid]::NewGuid().ToString("N")
Write-Host $env:LOGSTORE_INGEST_TOKEN
.\.venv\Scripts\python.exe app.py
```

Mở PowerShell thứ hai, dán đúng token đã in rồi chạy client mẫu trong thư mục gốc dự án:

```powershell
$env:LOGSTORE_INGEST_TOKEN = "<dan-token-tu-cua-so-thu-nhat>"
.\.venv\Scripts\python.exe examples\emit_logs.py
```

Mẫu gửi bốn sự kiện thuộc cùng `trace_id=ORDER-2026-1042`. Tìm trace đó trên dashboard. Server sẽ ghi thêm một audit event cho lần ingest thành công. Token chỉ dùng cục bộ; không commit token lên GitHub hoặc mở app ra Internet.

## Benchmark RocksDB và SQLite

```powershell
.\.venv\Scripts\python.exe bench.py --n 200000 --single 5000 --write-repeat 5 --repeat 1000 --json data\benchmark.json
```

Benchmark tạo DB tạm riêng, so sánh ghi theo lô/ghi từng sự kiện, truy vấn, đọc theo ID, dung lượng và retention. Ghi theo lô mặc định chạy trên 5 DB mới độc lập và báo min/median/max; truy vấn mặc định chạy 1.000 mẫu mỗi loại, báo p50/p95/p99 cùng số mẫu. JSON ghi cấu hình máy và điều kiện đo. Có thể giảm thời gian chạy để kiểm tra nhanh bằng `--write-repeat 2 --repeat 30`. SQLite dùng WAL với `synchronous=NORMAL`; RocksDB mặc định không fsync từng lần ghi. Ghi điều kiện đo khi đưa kết quả vào báo cáo. Kết quả tùy workload; không kết luận RocksDB nhanh hơn SQLite trong mọi thao tác.

## Kiểm thử

**Dừng web app trước khi chạy kiểm thử**: một số bài kiểm thử import entry point của ứng dụng và mở DB mặc định, nên có thể gặp `LOCK` nếu web vẫn đang chạy.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Kiểm thử được chia thành `tests/unit` cho lớp lưu trữ và `tests/integration` cho web app/CLI. Bộ kiểm thử bao gồm truy vấn index, độ nhất quán khi sửa/xóa/retention, đăng nhập và quyền, nhập/xuất, checkpoint/restore cùng các tình huống giới hạn.

## Mô hình lưu trữ

Event ID có 12 byte: timestamp mili giây big-endian (8 byte) và số thứ tự (4 byte). RocksDB sắp xếp key theo byte, vì vậy DB có thể quét sự kiện theo thời gian.

Nếu log đến muộn, hệ thống giữ timestamp của sự kiện và cấp sequence chưa dùng tại mili-giây đó. Vì vậy các key luôn được RocksDB sắp xếp theo thời điểm sự kiện, kể cả khi thứ tự ghi nhận khác thứ tự phát sinh.

| Column family | Vai trò |
|---|---|
| `default` | Sự kiện JSON theo event ID |
| `idx_level` | Index theo mức độ |
| `idx_source` | Index theo nguồn |
| `idx_trace` | Index theo trace ID |
| `meta` | Danh mục nguồn và bộ đếm tổng/số lượng theo mức độ |
| `users` | Tài khoản, password hash, vai trò và trạng thái |

Ghi, sửa, xóa sự kiện cập nhật bản ghi chính, index và bộ đếm trong một `WriteBatch`. Bộ đếm tổng/số lượng theo mức độ được nạp trực tiếp từ metadata; DB cũ tự tạo bộ đếm một lần khi mở lại. Retention quét ID trace theo iterator và xóa range theo từng nhóm, không giữ toàn bộ ID trace trong RAM. JSONL import xác thực toàn bộ vào tệp tạm rồi ghi bằng một `WriteBatch` nguyên tử; kích thước upload web được giới hạn 10 MB để chặn dữ liệu đầu vào quá lớn. Export duyệt một snapshot RocksDB bằng iterator dạng stream và dừng ở một triệu sự kiện. JSONL bỏ qua bản ghi audit hệ thống chuẩn từ `audit-service` để tệp có thể nhập lại mà không cho phép giả mạo audit; những sự kiện khác được giữ nguyên, kể cả bản ghi cũ có field `_audit` trùng tên. Checkpoint vẫn giữ toàn bộ audit. App lưu manifest đường dẫn để mở đúng DB sau khi chạy lại, trừ khi đường dẫn được cấu hình tường minh.

## Hạn chế cần nêu khi bảo vệ

- RocksDB khóa thư mục dữ liệu bằng file `LOCK`, nên chỉ một tiến trình được mở cùng DB. Dừng app web trước khi dùng CLI trên đúng thư mục DB đó.
- Tìm từ khóa trong nội dung chưa có inverted index.
- Số liệu theo khoảng thời gian vẫn phải quét index trong khoảng được hỏi; bộ đếm được cache chỉ áp dụng cho số tổng và tổng theo mức độ.
- Mật khẩu demo đã được băm, nhưng đây vẫn là ứng dụng chạy cục bộ cho đồ án, không phải cấu hình triển khai Internet.
- API nhập giới hạn 10 MB và 100.000 sự kiện mỗi tệp; xuất web giới hạn 1.000.000 sự kiện. CLI cũng giới hạn import 100.000 sự kiện.
- API tra cứu hiển thị tối đa 1.000 sự kiện mỗi trang; giao diện có nút trang trước/sau. Chỉ những route ghi mới chờ khóa ứng dụng; thao tác đọc dùng khả năng đọc đồng thời của RocksDB.
- Bộ đếm và audit event nằm trong cùng RocksDB nên được backup/restore cùng kho sự kiện; retention thường giữ lại audit event chuẩn từ `audit-service`. Nếu cần audit chống can thiệp độc lập, cần chuyển audit sang hệ thống lưu trữ riêng.
- Số liệu benchmark trong ghi chú nhóm cũ là từ máy khác. Báo cáo chỉ dùng số nhóm tự chạy, kèm cấu hình máy và điều kiện đo.

## Tài liệu trong dự án

- [Hướng dẫn nhóm và thao tác demo](docs/handbook/group-workflow.md)
- [Dàn ý báo cáo theo yêu cầu 50–60 trang](docs/report-preparation/outline.md)
- [Câu hỏi ôn bảo vệ](docs/defense/questions.md)
