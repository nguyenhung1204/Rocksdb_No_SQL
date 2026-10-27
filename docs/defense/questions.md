# Câu hỏi bảo vệ thường gặp (kèm hướng trả lời)

Đọc để hiểu, rồi tự trả lời bằng lời của mình. Trả lời học thuộc sẽ lộ ngay khi bị hỏi xoáy.

## A. Về RocksDB và lý thuyết

**1. RocksDB là gì, khác gì Redis và SQLite?**
Thư viện CSDL key-value nhúng, lưu trên đĩa, tối ưu cho ghi nhanh (LSM-tree). Redis chủ yếu giữ dữ liệu trong RAM và chạy như server riêng. SQLite cũng nhúng và lưu đĩa nhưng là CSDL quan hệ dùng SQL trên B-tree. RocksDB không có SQL và không có index phụ tự động.

**2. LSM-tree hoạt động thế nào?**
Ghi: nối vào WAL (ghi tuần tự để bền vững) và đưa vào memtable trong RAM. Memtable đầy thì flush thành file SST bất biến đã sắp xếp. Compaction chạy nền, gộp các SST và loại bản ghi cũ/đã xóa. Đọc: tìm ở memtable, rồi các tầng SST từ mới đến cũ; Bloom filter giúp bỏ qua file không chứa key.

**3. Vì sao ghi nhanh?**
Mọi thao tác ghi đều là ghi tuần tự (WAL + flush), không phải cập nhật tại chỗ trên cây B-tree như nhiều CSDL truyền thống. Cái giá là compaction chạy nền (write amplification) và đọc có thể phải kiểm tra nhiều tầng (read amplification).

**4. Write / read / space amplification là gì?**
Lượng dữ liệu thực ghi xuống đĩa so với dữ liệu ứng dụng ghi; lượng dữ liệu phải đọc so với dữ liệu cần; dung lượng đĩa so với dữ liệu thật. Chỉnh compaction và kích thước tầng là đánh đổi giữa ba thứ này.

**5. WAL để làm gì? Mất điện thì sao?**
WAL cho phép khôi phục memtable sau sự cố. Mặc định không fsync mỗi lần ghi nên *tiến trình* bị kill thì thường an toàn, nhưng *mất điện* có thể mất vài lần ghi cuối; muốn chắc chắn phải bật `sync=True` (chậm hơn). Nhóm đã có tùy chọn `--sync`.

**6. Column family là gì, sao dùng?**
Các "bảng" logic trong cùng một DB. Bản demo dùng sáu CF: `default` giữ sự kiện; `idx_level`, `idx_source`, `idx_trace` phục vụ truy vấn; `meta` giữ danh mục nguồn; `users` giữ tài khoản và password hash. Các thao tác sự kiện cập nhật bản ghi chính cùng index bằng `WriteBatch`.

**7. Bloom filter, block cache, compression để làm gì?**
Bloom filter: tránh đọc file SST không chứa key (giảm read amplification, chủ yếu có lợi cho `get`). Block cache: giữ block đã đọc trong RAM. LZ4: nén nhẹ, giảm dung lượng với chi phí CPU nhỏ (benchmark của nhóm cho thấy dung lượng nhỏ hơn SQLite nhiều lần).

**8. DeleteRange khác xóa từng key thế nào?**
Chỉ ghi range tombstone cho khoảng key nên phù hợp xóa dữ liệu cũ theo thời gian. Dung lượng chỉ giảm sau compaction. Retention cần xóa vùng tương ứng trong dữ liệu chính và ba index sự kiện.

**9. Checkpoint khác copy thư mục thế nào?**
Checkpoint cho bản sao nhất quán tại một thời điểm, dùng hard-link file SST bất biến nên nhanh và không dừng ghi. Copy thư mục đang chạy có thể sao ra trạng thái hỏng.

**10. Ai dùng RocksDB trong thực tế?**
Dùng làm engine lưu trữ bên dưới nhiều hệ thống, ví dụ MyRocks (MySQL của Meta), TiKV, state store của Kafka Streams, state backend của Flink. (Hãy kiểm tra lại nguồn trước khi đưa vào báo cáo và trích dẫn.)

## B. Về thiết kế của nhóm

**11. Vì sao khóa là timestamp + seq, big-endian?**
RocksDB sắp xếp key theo byte. Big-endian làm thứ tự byte trùng thứ tự số nên duyệt theo khoảng thời gian là quét tuần tự, không cần sort. `seq` tránh hai sự kiện cùng mili-giây đè nhau.

**12. Đồng hồ hệ thống bị lùi thì sao? Khởi động lại thì sao?**
Timestamp trong event ID giữ thời điểm sự kiện. Nếu log đến muộn, hệ thống tìm `seq` chưa dùng tại timestamp đó; với timestamp mới nhất, hệ thống tiếp tục sequence từ key cuối khi mở lại DB. RocksDB vẫn sắp xếp khóa theo thời điểm sự kiện. Kiểm thử bao gồm cùng mili-giây, log đến muộn và mở lại DB.

**13. Index phụ có thể lệch với dữ liệu không?**
Ghi, sửa, xóa một sự kiện đều cập nhật bản ghi chính và index mức/nguồn/trace trong một WriteBatch. Có kiểm thử đối chiếu kết quả truy vấn sau sửa, xóa và retention.

**14. Lọc `level >= WARN` làm thế nào mà vẫn nhanh và đúng thứ tự?**
Duyệt index của từng mức WARN, ERROR, FATAL (mỗi cái đã sắp xếp theo id) rồi gộp bằng `heapq.merge`. Test đối chiếu với kết quả tính tay.

**15. Tại sao `--contains` chậm?**
Không có index cho nội dung, phải đọc từng bản ghi để lọc. Đó là hạn chế bản chất của KV store; hướng giải quyết là thêm inverted index hoặc dùng công cụ tìm kiếm.

**16. `trace_id` được truy vấn như thế nào?**
`fields.trace_id` được ghi thêm vào `idx_trace`. Khóa dùng tiền tố trace ID, byte `0x00` phân cách và event ID phía sau, nên truy vấn một trace chỉ quét nhóm key có tiền tố tương ứng.

**17. Value dùng JSON, sao không dùng định dạng nhị phân?**
JSON dễ đọc/gỡ lỗi và linh hoạt về `fields`. Đánh đổi: tốn dung lượng và CPU hơn; có thể thay bằng MessagePack/Protobuf nếu cần tối ưu (hướng phát triển).

**18. Tại sao hai tiến trình không mở chung một DB được?**
RocksDB là CSDL nhúng, khóa thư mục bằng file `LOCK`. Muốn nhiều tiến trình dùng chung thì phải có một tiến trình dịch vụ (như `app.py`) làm cổng truy cập, hoặc mở chế độ chỉ đọc/secondary.

## C. Về đánh giá

**19. Vì sao SQLite nhanh hơn ở một số truy vấn trong benchmark?**
Nêu đúng thực tế: ở truy vấn khoảng nhỏ và truy vấn theo nguồn, SQLite nhanh hơn trên máy nhóm đo. Giải thích hợp lý: binding Python và giải mã JSON cho từng dòng; index theo nguồn của RocksDB phải đọc thêm bản ghi chính cho từng id. RocksDB thắng ở ghi lẻ, xóa hàng loạt và dung lượng. Không phủ nhận kết quả, chỉ ra phạm vi phù hợp của mỗi công cụ.

**20. Benchmark có công bằng không?**
Cùng dữ liệu, cùng ba chỉ mục, SQLite bật WAL và `synchronous=NORMAL`, lấy trung vị nhiều lần. Hạn chế: một máy, binding Python, cache hệ điều hành, chưa đo với fsync mỗi lần ghi.

**21. Hệ thống mở rộng quy mô thế nào?**
Bản này là lưu trữ cục bộ một máy (đúng đề tài). Mở rộng: phân mảnh theo thời gian/nguồn, hoặc đẩy log sang hệ thống tập trung; các CSDL phân tán như TiKV dùng RocksDB làm engine bên dưới.

**22. Nếu làm lại hoặc có thêm thời gian thì cải tiến gì?**
Chọn 2–3 mục trong "hướng phát triển": inverted index cho nội dung, TTL tự động, mã hóa nhị phân cho value, phân trang bằng con trỏ, đo ảnh hưởng của từng option RocksDB.

## D. Về ứng dụng demo

**23. Vì sao cần tài khoản và phân quyền?**
Tài khoản admin quản lý người dùng và khôi phục checkpoint; operator thao tác dữ liệu; viewer chỉ tra cứu. Mật khẩu được băm trước khi ghi vào CF `users`, còn các API ghi yêu cầu phiên đăng nhập và CSRF token.

**24. Nhập và xuất JSONL bảo toàn những gì?**
JSONL giữ level, source, msg, fields và timestamp. Khi nhập, RocksDB cấp event ID mới để tránh ghi đè key đã tồn tại. Ứng dụng kiểm tra toàn bộ tệp trước khi ghi để tránh nhập dở.

**25. Vì sao khôi phục không ghi đè trực tiếp thư mục DB đang mở?**
RocksDB giữ file `LOCK` khi tiến trình đang mở DB. Demo phục hồi checkpoint vào một thư mục mới, mở kiểm tra thành công rồi chuyển ứng dụng sang DB mới. Snapshot cũ và thư mục DB cũ vẫn có thể giữ để đối chiếu.
