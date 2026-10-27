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

Ứng dụng tự tạo khóa phiên mới mỗi lần chạy, nên có thể cần đăng nhập lại sau khi khởi động. Nếu muốn giữ phiên, đặt `LOGSTORE_SECRET_KEY` bằng chuỗi bí mật ngẫu nhiên của riêng nhóm và dùng lại cùng giá trị trong các lần chạy; không đưa giá trị này lên GitHub. App mặc định chỉ lắng nghe tại `127.0.0.1`.

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

Restore trên web sao checkpoint sang thư mục mới `data/restores/restore-...` rồi chuyển DB đang dùng trong tiến trình hiện tại. **Đường dẫn sau restore chưa được lưu thành cấu hình khởi động**: chạy lại `app.py` với cấu hình mặc định sẽ mở `data/webdb` ban đầu.

Để tiếp tục dùng DB đã restore sau khi dừng/chạy lại:

1. Trước khi dừng, sao chép đường dẫn hiển thị ở **“Thư mục DB đang dùng”** trên dashboard. Có thể lấy cùng giá trị ở trường `path` khi mở `/api/stats` sau khi đăng nhập, hoặc `active_path` trong JSON trả về từ API restore.
2. Dừng app bằng Ctrl+C. Đặt hai biến dưới đây trong PowerShell tại thư mục dự án; thay phần giữ chỗ bằng đường dẫn vừa sao chép:

```powershell
$env:LOGSTORE_PATH = "<duong-dan-DB-da-restore>"
$env:LOGSTORE_BACKUP_PATH = "data\backups"
.\.venv\Scripts\python.exe app.py
```

`LOGSTORE_BACKUP_PATH` phải tiếp tục trỏ tới thư mục checkpoint gốc; nếu trước đó dùng đường dẫn tùy chỉnh, dùng lại đường dẫn đó thay cho `data\backups`. Các biến `$env:` chỉ áp dụng cho PowerShell hiện tại; mở cửa sổ mới cần đặt lại. Muốn trở về DB mặc định, dừng app, xóa cấu hình phiên bằng `Remove-Item Env:LOGSTORE_PATH -ErrorAction SilentlyContinue` rồi chạy lại.

## Benchmark RocksDB và SQLite

```powershell
.\.venv\Scripts\python.exe bench.py --n 200000 --single 5000 --repeat 30 --json data\benchmark.json
```

Benchmark tạo DB tạm riêng, so sánh ghi theo lô/ghi từng sự kiện, truy vấn, đọc theo ID, dung lượng và retention. SQLite dùng WAL với `synchronous=NORMAL`; RocksDB mặc định không fsync từng lần ghi. Ghi cấu hình máy, số bản ghi và điều kiện đo khi đưa kết quả vào báo cáo. Kết quả tùy workload; không kết luận RocksDB nhanh hơn SQLite trong mọi thao tác.

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
| `meta` | Danh mục nguồn đã gặp |
| `users` | Tài khoản, password hash, vai trò và trạng thái |

Ghi, sửa, xóa sự kiện cập nhật bản ghi chính và index trong một `WriteBatch`. Truy vấn chuỗi trong nội dung không có index nên cần đọc và lọc bản ghi. Checkpoint nhất quán của RocksDB được sao sang thư mục mới khi restore; ứng dụng chỉ đổi DB đang hoạt động sau khi mở checkpoint thành công.

## Hạn chế cần nêu khi bảo vệ

- RocksDB khóa thư mục dữ liệu bằng file `LOCK`, nên chỉ một tiến trình được mở cùng DB. Dừng app web trước khi dùng CLI trên đúng thư mục DB đó.
- Tìm từ khóa trong nội dung chưa có inverted index.
- Danh sách trace ID duy nhất được giữ trong bộ nhớ để cập nhật retention; ứng dụng phù hợp với demo cục bộ, cần đánh giá thêm mức dùng RAM nếu dữ liệu có lượng trace ID rất lớn.
- Mật khẩu demo đã được băm, nhưng đây vẫn là ứng dụng chạy cục bộ cho đồ án, không phải cấu hình triển khai Internet.
- API nhập giới hạn 10 MB và 100.000 sự kiện mỗi tệp; xuất web giới hạn 1.000.000 sự kiện. CLI cũng giới hạn import 100.000 sự kiện.
- API tra cứu trả tối đa 1.000 sự kiện mỗi lần, chưa có cursor phân trang; thống kê còn quét lại index, import/export web còn xử lý toàn bộ tệp/kết quả trong bộ nhớ.
- Sau restore, đường dẫn DB đang hoạt động chỉ đổi trong bộ nhớ của tiến trình. Cần đặt lại `LOGSTORE_PATH` khi khởi động nếu muốn tiếp tục dùng bản đã khôi phục, như hướng dẫn ở trên.
- Số liệu benchmark trong ghi chú nhóm cũ là từ máy khác. Báo cáo chỉ dùng số nhóm tự chạy, kèm cấu hình máy và điều kiện đo.

## Tài liệu trong dự án

- [Hướng dẫn nhóm và thao tác demo](docs/handbook/group-workflow.md)
- [Dàn ý báo cáo theo yêu cầu 50–60 trang](docs/report-preparation/outline.md)
- [Câu hỏi ôn bảo vệ](docs/defense/questions.md)
