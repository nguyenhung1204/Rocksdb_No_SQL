# Dàn ý báo cáo (mục tiêu 50–60 trang theo thang điểm)

Thang điểm trong PDF của giảng viên yêu cầu báo cáo dài 50–60 trang, Times New Roman cỡ 13, giãn dòng 1,5. Dàn ý này thay thế ghi chú cũ 35–45 trang. Đây mới là khung nội dung; nhóm cần bổ sung kết quả và ảnh chụp từ máy của mình.

Phân bổ dự kiến: phần đầu 3–4 trang, Chương 1 (4), Chương 2 (11–12), Chương 3 (8–9), Chương 4 (7–8), Chương 5 (6–7), Chương 6 (7–8), Chương 7 (2–3), tài liệu tham khảo và phụ lục (3–5). Tổng mục tiêu là 51–60 trang.

Mỗi chương ghi người phụ trách chính. Viết bằng lời của nhóm, đừng chép nguyên văn tài liệu.

## Trang bìa, lời cảm ơn, mục lục, danh mục hình/bảng/viết tắt

## Chương 1. Giới thiệu (TV3, ~4 trang)
- 1.1 Bối cảnh: nhật ký và sự kiện trong hệ thống phần mềm; vì sao cần lưu trữ cục bộ tốc độ cao (agent trên thiết bị, edge, ứng dụng desktop, service cần đệm log trước khi gửi đi).
- 1.2 Bài toán và yêu cầu: ghi liên tục khối lượng lớn, truy vấn theo thời gian/mức độ/nguồn, xóa dữ liệu cũ, sao lưu.
- 1.3 Mục tiêu và phạm vi đề tài.
- 1.4 Phân công và phương pháp thực hiện của nhóm (bảng phân công).

## Chương 2. Cơ sở lý thuyết (TV3, ~11–12 trang)
- 2.1 Tổng quan NoSQL và các mô hình dữ liệu; vị trí của Key-Value so với Document, Column-family, Graph.
- 2.2 CSDL Key-Value: thao tác Put/Get/Delete/Scan, ưu và nhược điểm.
- 2.3 RocksDB: lịch sử (từ LevelDB), đặc điểm *nhúng*, người dùng thực tế (nêu vài ví dụ có nguồn).
- 2.4 **LSM-tree** (hình vẽ luồng ghi): WAL → memtable → flush thành SST → compaction theo tầng; luồng đọc: memtable → các tầng SST + Bloom filter.
- 2.5 Ba loại khuếch đại: write / read / space amplification và đánh đổi giữa chúng.
- 2.6 Các tính năng RocksDB dùng trong đề tài: Column Family, WriteBatch, Iterator/seek, DeleteRange, Checkpoint, Compression, Bloom filter, block cache.
- 2.7 So sánh ngắn RocksDB với SQLite, Redis, MongoDB (bảng: mô hình, nhúng/server, lưu đĩa/RAM, mạnh ở đâu, hạn chế).

## Chương 3. Phân tích và thiết kế hệ thống (TV1, ~8–9 trang)
- 3.1 Kiến trúc tổng thể (hình: ứng dụng → lớp `LogStore` → RocksDB → đĩa).
- 3.2 Mô hình sự kiện (level, source, msg, fields) và vì sao chọn JSON cho value.
- 3.3 **Thiết kế khóa** `event_id = timestamp(8B) + seq(4B)`, big-endian: lý do, xử lý trùng mili-giây, đồng hồ lùi, khởi động lại.
- 3.4 Thiết kế 6 column family: `default`, `idx_level`, `idx_source`, `idx_trace`, `meta`, `users` (bảng + hình bố cục key).
- 3.5 Thiết kế các thao tác: ghi nguyên tử (WriteBatch), truy vấn theo thời gian/mức/nguồn/trace, sửa/xóa đồng bộ chỉ mục, retention (DeleteRange), JSONL, backup/restore (Checkpoint).
- 3.6 Thiết kế đăng nhập, phân quyền admin/operator/viewer, REST API và giao diện (TV2 viết phần này).
- 3.7 Lựa chọn cấu hình (compression, bloom, memtable...) và lý do.

## Chương 4. Cài đặt (TV1 + TV2, ~7–8 trang)
- 4.1 Môi trường, công cụ, thư viện (phiên bản cụ thể).
- 4.2 Cấu trúc mã nguồn và trách nhiệm của `logstore/`, `webapp/`, `templates/`, `tests/`, `docs/`.
- 4.3 Trích đoạn mã quan trọng, **có giải thích** (đừng dán hết file): sinh id, `put_many`, `_scan`, `query`, `purge_older_than`, `backup`.
- 4.4 Xử lý lỗi và tình huống biên đã gặp (ví dụ: không mở lại được DB sau khi đóng vì handle column family còn giữ khóa — đã sửa trong `close()`; khóa `LOCK` khi hai tiến trình cùng mở một DB).
- 4.5 Hướng dẫn cài đặt và sử dụng (rút gọn từ README).

## Chương 5. Ứng dụng minh họa và kiểm thử (TV2 + TV1, ~6–7 trang)
- 5.1 Kịch bản demo (xem bên dưới) kèm ảnh chụp màn hình CLI và web.
- 5.2 Kiểm thử: nhóm kiểm thử lõi lưu trữ và kiểm thử tích hợp web/CLI; nêu mục tiêu từng nhóm và cách đối chiếu truy vấn với tính tay (brute-force).
- 5.3 Kết quả kiểm thử.

## Chương 6. Đánh giá hiệu năng (TV3, ~7–8 trang)
- 6.1 Phương pháp: dữ liệu, kích thước, cấu hình máy, số lần chạy, cách công bằng với SQLite (cùng 3 index).
- 6.2 Kết quả: bảng + biểu đồ cột (ghi lô, ghi lẻ, truy vấn, đọc theo id, dung lượng, xóa).
- 6.3 **Phân tích trung thực**: chỗ RocksDB hơn, chỗ SQLite hơn, giải thích nguyên nhân, mối đe dọa tới độ tin cậy (binding Python, một máy, đĩa, bộ nhớ đệm hệ điều hành).
- 6.4 (Điểm cộng) Thí nghiệm tuning: đổi một option, đo trước/sau.

## Chương 7. Kết luận và hướng phát triển (TV3 + cả nhóm, ~2–3 trang)
- Kết quả đạt được so với mục tiêu.
- Hạn chế: không có tìm kiếm toàn văn (`contains` phải quét), chỉ một tiến trình mở DB, index tự bảo trì, không phân tán.
- Hướng phát triển: inverted index cho nội dung, TTL tự động, nén/đóng gói log theo giờ, đẩy log sang hệ thống trung tâm, dùng bản C++/Rust/Java nếu cần hiệu năng cao hơn.

## Tài liệu tham khảo và phụ lục (~3–5 trang)

Tài liệu tham khảo theo chuẩn của khoa. Phụ lục có thể gồm bảng phân công, biên bản họp, hướng dẫn cài đặt, mẫu JSONL, kết quả benchmark thô do nhóm tự chạy và bảng đối chiếu yêu cầu rubric.

---

# Kịch bản demo 7–10 phút (TV2 trình bày, TV1 và TV3 hỗ trợ)

1. **(1 phút)** Giới thiệu bài toán + sơ đồ kiến trúc.
2. **(1 phút)** `python cli.py --db data/logdb gen --n 200000` → nói tốc độ đo được trên máy nhóm.
3. **(1 phút)** `python cli.py --db data/logdb stats` → giải thích 6 column family và vai trò của từng nhóm key.
4. **(2 phút)** Truy vấn theo thời gian, mức, nguồn, `trace_id`, rồi `--contains`; nói rõ `contains` phải quét nội dung.
5. **(2 phút)** Đăng nhập web; thêm, tìm, sửa và xóa sự kiện; thử vai trò viewer; mở trang quản lý người dùng.
6. **(1 phút)** Xuất JSONL, nhập lại tệp mẫu, tạo checkpoint rồi khôi phục snapshot.
7. **(1 phút)** `purge --older-than 12h --compact` trên DB demo riêng; giải thích vì sao compaction mới thu hồi dung lượng.
8. **(1 phút)** Bảng benchmark nhóm tự đo và kết luận phù hợp theo workload.

Chuẩn bị sẵn dữ liệu từ trước, quay video dự phòng, và tắt web trước khi dùng CLI trên cùng một thư mục DB. Không dùng thao tác purge trên dữ liệu nhóm muốn giữ.
