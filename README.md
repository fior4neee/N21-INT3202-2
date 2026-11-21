# N21-INT3202-2
Hqtcsdl

để chạy split_ipums_csv.py , upload ipumsi_00001.csv lên data/ xong chạy

data guide

cần để ý nhất 2 cột 'PERNUM' và 'SERIAL'
SERIAL - Mã định danh hộ gia đình trong mẫu khảo sát. Mã này lặp lại trên mỗi dòng dữ liệu của từng cá nhân thuộc hộ gia đình đó.
PERNUM - Số người ở 1 căn hộ. **Số thứ tự của cá nhân trong hộ gia đình. Cùng với SERIAL, thông tin này giúp xác định một cá nhân trong mẫu khảo sát.**

Dưới đây là các tên cột viết tắt hoặc không rõ nghĩa ngay, **theo thứ tự xuất hiện** trong tệp `ipumsi_00001.csv`. Các cột có ký tự `H` mô tả về hộ gia đình; các cột có ký tự `P` mô tả về từng cá nhân trong hộ đó.

### Các trường thông tin về hộ gia đình

| Cột | Ý nghĩa |
|---|---|
| `SAMPLE` | Mã định danh cho mẫu điều tra hoặc tổng điều tra. |
| `SERIAL` | Mã định danh hộ gia đình trong phạm vi mẫu. Mã này lặp lại trên mỗi dòng dữ liệu của từng cá nhân thuộc hộ gia đình đó. |
| `SUBSAMP` | Mã định danh mẫu phụ (subsample). |
| `ENUMOP` | Hoạt động điều tra (Enumeration operation): mã số của một đợt tổng điều tra hoặc hoạt động thu thập dữ liệu. |
| `FORMTYPE` | Loại biểu mẫu được sử dụng cho hộ gia đình hoặc đợt điều tra đó. |
| `ENUMTYPE` | Mã loại hình hoặc phương pháp điều tra. |
| `ENUMMO` | Tháng thực hiện điều tra. |
| `ENUMHH` | Mã liên quan đến hộ gia đình trong quá trình điều tra. Ý nghĩa cụ thể của danh mục này phụ thuộc vào tài liệu hướng dẫn mã hóa (codebook) của Tổng điều tra Việt Nam năm 2019. |
| `SAMPMETH` | Phương pháp chọn mẫu. |
| `SAMPFRCT` | Tỷ lệ chọn mẫu (sampling fraction): tỷ lệ hoặc mức được sử dụng để chọn mẫu. |
| `SAMPUNIT` | Đơn vị chọn mẫu (sampling unit) — đơn vị được chọn theo thiết kế chọn mẫu. |
| `SAMPDRW` | Mã chọn mẫu hoặc mã phương thức chọn mẫu. |
| `REGIONW` | Phân loại theo các khu vực rộng lớn trên thế giới. |
| `GEOLEV1`, `GEOLEV2` | Mã định danh địa lý cấp 1 và cấp 2. Đây là các mã địa lý, không phải tên gọi. |
| `POPDENSGEO1`, `POPDENSGEO2` | Mật độ dân số theo đơn vị địa lý cấp 1 và cấp 2. |
| `AREAMOLLWGEO1`, `AREAMOLLWGEO2` | Diện tích theo đơn vị địa lý cấp 1 và cấp 2, được tính toán dựa trên phép chiếu bản đồ Mollweide. |
| `GEO1_VN`, `GEO2_VN` | Mã định danh địa lý cho các đơn vị cấp 1 và cấp 2 tại Việt Nam. |
| `GEO1_VN2019`, `GEO2_VN2019` | Mã địa lý cho các đơn vị này theo hệ thống phân loại năm 2019 của Việt Nam. |
| `REGNVN` | Mã khu vực tại Việt Nam. |
| `OWNERSHIPD` | Phiên bản chi tiết của `OWNERSHIP` — phân loại cụ thể hơn về quyền sở hữu nhà ở. |

### Các trường thông tin về cá nhân

| Cột | Ý nghĩa |
|---|---|
| `PERNUM` | Số thứ tự của cá nhân trong hộ gia đình. Kết hợp với `SERIAL`, mã này giúp xác định một cá nhân cụ thể trong mẫu dữ liệu. |
| `PERWT` | Trọng số cá nhân: một trọng số thống kê được sử dụng khi ước tính tổng dân số hoặc các tỷ lệ. |
| `RELATE` | Mối quan hệ của cá nhân đó với người tham chiếu hoặc chủ hộ. |
| `RELATED` | Phiên bản chi tiết của `RELATE`. |
| `MARST` | Tình trạng hôn nhân. |
| `MARSTD` | Phiên bản chi tiết của `MARST`. |
| `AGEMARR` | Độ tuổi khi kết hôn. |
| `MARRYR` | Năm kết hôn. |
| `BTHCERT` | Tình trạng giấy khai sinh. |
| `RELIGIOND` | Phiên bản chi tiết của `RELIGION`. |
| `ETHNICVN` | Mã nhóm dân tộc theo hệ thống phân loại của Việt Nam. |
| `LIT` | Tình trạng biết đọc biết viết. |
| `EDATTAIN` | Trình độ học vấn. |
| `EDATTAIND` | Phiên bản chi tiết của `EDATTAIN`. |
| `YRSCHOOL` | Số năm đi học. |
| `EDUCVN` | Phân loại giáo dục dành riêng cho Việt Nam. |
| `EMPSTAT` | Tình trạng việc làm. |
| `EMPSTATD` | Phiên bản chi tiết của `EMPSTAT`. |
| `LABFORCE` | Tình trạng tham gia lực lượng lao động. |
| `CLASSWK` | Nhóm người lao động (ví dụ: một phân loại việc làm tổng quát). |
| `CLASSWKD` | Phiên bản chi tiết của `CLASSWK`. |
| `WRKAVAIL` | Khả năng sẵn sàng làm việc. |
| `MIGRATE5` | Thông tin di cư trong giai đoạn năm năm trước tổng điều tra. |
| `GEOMIG1_5` | Mã địa lý cấp 1 về nơi xuất phát của người di cư, tương ứng với giai đoạn năm năm đó. |
| `MIGCAUSE` | Nguyên nhân hoặc lý do di cư. |

Một số gợi ý về cách đặt tên giúp giải mã các biến còn lại: `ENUM` nghĩa là *điều tra/liệt kê* (enumeration); `SAMP` nghĩa là *mẫu/lấy mẫu* (sample/sampling); `GEO` nghĩa là *địa lý* (geography); hậu tố `D` thường biểu thị phiên bản chi tiết hơn của một biến; và `_5` cho biết khoảng thời gian tham chiếu là năm năm. Những tên gọi này giải thích các khái niệm chung, nhưng **các giá trị số thực chất là các mã (code)**, chứ không hẳn là các phép đo hay nhãn mô tả trực tiếp. Chẳng hạn, bạn cần có tài liệu tra cứu mã (codebook) để biết mỗi giá trị trong `ENUMHH` hay `MIGCAUSE` đại diện cho điều gì. Tệp CSV và tài liệu hướng dẫn được đề cập ở đây không bao gồm các định nghĩa mã đó, vì vậy tôi đã tránh việc tự suy đoán ý nghĩa của chúng.
---

## A5: Quản lý nhân khẩu, so sánh MongoDB và MySQL

Cùng một bộ dữ liệu (IPUMS Việt Nam 2019: 1.001.120 hộ, 3.550.482 nhân khẩu) được nạp vào cả MySQL và MongoDB. Bài làm chia thành các **kịch bản nghiệp vụ thực tế** của công tác quản lý dân cư. Mỗi kịch bản có một bản MySQL và một bản MongoDB làm **cùng một việc**, và được đo thời gian trên cùng một máy.

### Mô hình dữ liệu

| | MySQL | MongoDB |
|---|---|---|
| Hộ | bảng `hokhau` (PK `SERIAL`) | collection `hokhau`, mỗi hộ là 1 document, `_id = SERIAL` |
| Nhân khẩu | bảng `nhankhau` (PK `SERIAL, PERNUM`, FK tới `hokhau`) | mảng `nhankhau` **nhúng** trong document hộ, giống một cuốn sổ hộ khẩu |
| Biến động cư trú | bảng `biendong_cutru` | collection `biendong_cutru` |
| Index | `GEO1_VN`, `AGE`, `MIGRATE5` | `GEO1_VN`, `nhankhau.AGE`, `nhankhau.MIGRATE5` |

Tên trường giữ nguyên như tên cột MySQL để dễ đặt hai câu lệnh cạnh nhau khi so sánh.

### Các kịch bản

| Mã | Nghiệp vụ | MySQL | MongoDB |
|---|---|---|---|
| **KB1** | **Thu thập dữ liệu dân cư** | | |
| KB1.1 | Nạp hàng loạt toàn bộ dữ liệu điều tra | `mysql/hokhau.sql`, `mysql/nhankhau.sql` (`LOAD DATA`) | `mongodb/load_mongo.py` (`insertMany`) |
| KB1.2 | Đăng ký hộ mới (hộ + các nhân khẩu + ghi biến động) | `mysql/kb1_dang_ky_ho_moi.sql` | `mongodb/kb1_dang_ky_ho_moi.js` |
| **KB2** | **Cập nhật dữ liệu dân cư** | | |
| KB2.1 | Cập nhật 1 người: đăng ký kết hôn | `mysql/kb2_cap_nhat.sql` | `mongodb/kb2_cap_nhat.js` (`arrayFilters`) |
| KB2.2 | Cập nhật hàng loạt: tăng tuổi toàn bộ dân 1 tỉnh | `UPDATE ... JOIN` | `updateMany` + `$[]` |
| **KB3** | **Đăng ký, quản lý cư trú** | `mysql/kb3_cu_tru.sql` | `mongodb/kb3_cu_tru.js` |
| KB3.1 | Khai sinh: thêm trẻ vào hộ | transaction 4 lệnh | 1 lệnh cập nhật nguyên tử trên 1 document |
| KB3.2 | Chuyển hộ khẩu: chuyển 1 người từ hộ A sang hộ B | transaction | transaction (sửa 2 document) |
| KB3.3 | Tách hộ: một phần thành viên lập hộ mới | transaction, `ROW_NUMBER()` | transaction |
| KB3.4 | Xóa đăng ký thường trú (khai tử, ra nước ngoài) | transaction | 1 lệnh cập nhật nguyên tử |
| **KB4** | **Tra cứu, thống kê** | | `mongodb/kb4_tra_cuu.js` |
| KB4.1 | Tra cứu 1 hộ và các nhân khẩu | `q1_point_lookup.sql` | `$match _id` (không cần JOIN) |
| KB4.2 | Số người có việc làm theo tuổi | `q2_aggregation.sql` | `$unwind` + `$group` |
| KB4.3 | Danh sách người 18–60 tuổi của 1 tỉnh | `q3_range.sql` | `$match` + `$unwind` |
| KB4.4 | Người chuyển đến tỉnh trong 5 năm, theo tỉnh nơi đi | `q5_nhap_cu.sql` | `$group` theo `GEOMIG1_5` |
| KB4.5 | Tháp dân số: tỉnh × giới tính × nhóm 5 tuổi | `q6_thap_dan_so.sql` | `$group` toàn bộ |

Mỗi nghiệp vụ ở KB1.2 và KB3 đều ghi một dòng vào `biendong_cutru` (loại: `DANG_KY_HO_MOI`, `KHAI_SINH`, `CHUYEN_DI`, `CHUYEN_DEN`, `TACH_HO`, `XOA_DANG_KY`), nên có thể tra lại lịch sử cư trú của từng hộ.

### Cài đặt và chạy

Cần MySQL 8.0 (service `MySQL80`) và MongoDB 8.0 (service `MongoDB`) cùng cài trên một máy. **Không dùng Atlas để so sánh**: Atlas nằm ở xa nên thời gian đo bị cộng thêm độ trễ mạng, còn MySQL chạy ngay trên máy.

```powershell
pip install -r a5/requirements.txt
python a5/split_ipums_csv.py                  # tách ipumsi_00001.csv -> hokhau.csv, nhankhau.csv

# MySQL (Workbench hoặc mysql client), theo thứ tự:
#   a5/mysql/hokhau.sql -> nhankhau.sql -> kb0_chuan_bi.sql (bảng biendong_cutru + index)
# MongoDB (thời gian nạp in ra là kết quả của KB1.1):
python a5/mongodb/load_mongo.py

# Đo hiệu năng, cả hai hệ:
$env:MYSQL_PASSWORD = "mat_khau_root"         # thêm MYSQL_USER/MYSQL_HOST/MONGO_URI nếu khác mặc định
python a5/bench/bench.py --quick              # chạy thử nhanh
python a5/bench/bench.py --explain            # chạy đầy đủ + lưu execution plan
python a5/bench/bench.py --only KB3 KB4.4     # chỉ chạy một số kịch bản
```

Kết quả được ghi vào `a5/results/bench_<thời gian>.csv`, gồm các cột median, p95, min, max (ms), số dòng và cột cho biết kết quả hai hệ có khớp nhau không. Với `--explain`, thư mục `a5/results/explain_<thời gian>/` chứa `EXPLAIN ANALYZE` của MySQL và `explain("executionStats")` của MongoDB cho từng truy vấn đọc. `q4_index_explain.sql` vẫn dùng để so sánh trước và sau khi có index trên MySQL.

Các file `.sql`/`.js` dùng để minh họa từng lệnh trong Workbench hoặc mongosh (mongosh có sẵn trong tab dưới cùng của Compass: `load("C:/GitHub/N21-INT3202-2/a5/mongodb/kb3_cu_tru.js")`). Còn `bench.py` chứa đúng các lệnh đó và dùng để đo thời gian.

Dung lượng: khi nạp đủ dữ liệu, mỗi hệ chiếm khoảng 1 GB (dữ liệu và index). `bench.py` in dung lượng của cả hai ở cuối mỗi lần chạy.

### Đo như thế nào cho công bằng

- Cùng máy, cùng dữ liệu, cùng index. Trước khi đo, `bench.py` kiểm tra hai hệ có cùng số hộ không.
- Thời gian được đo từ phía client (Python), **gồm cả thời gian lấy hết các dòng kết quả**. Mỗi truy vấn đọc được chạy khởi động 1 lần trước khi đo, và kết quả báo cáo là median của nhiều lần chạy. Với truy vấn trả về nhiều dòng (KB4.3, khoảng 200 nghìn dòng), phần lớn thời gian là do Python giải mã kết quả. Để xem thời gian thực thi trên server, đọc file explain.
- Độ bền khi ghi: MySQL mặc định flush log ở mỗi commit (`innodb_flush_log_at_trx_commit=1`), nên MongoDB được đặt `j=true` (chờ ghi journal) cho tương đương. Có thể bỏ bằng `--no-journal` để thấy khác biệt.
- Dữ liệu thật không bị thay đổi: KB1.2 và KB3 chỉ thao tác trên các hộ thử nghiệm có `SERIAL >= 900000000000`, được xóa khi bắt đầu và khi kết thúc. KB2 sửa dữ liệu thật nhưng trả lại giá trị cũ sau mỗi lần chạy (phần trả lại không tính giờ).
- Các truy vấn đọc được chạy trước KB2.2, để lệnh cập nhật cả tỉnh không làm thay đổi cache khi đo truy vấn đọc.

### Transaction của MongoDB

MongoDB chỉ hỗ trợ transaction nhiều document trên **replica set**. Service MongoDB mặc định trên Windows là standalone; khi đó `bench.py` vẫn chạy nhưng in cảnh báo, và KB1.2/KB3 sẽ ghi **không có transaction** (cột `mongo_transaction = không` trong CSV). Để so sánh đúng với transaction của MySQL, hãy chuyển thành replica set 1 node:

1. Mở `C:\Program Files\MongoDB\Server\8.0\bin\mongod.cfg` bằng quyền admin, thêm:
   ```yaml
   replication:
     replSetName: rs0
   ```
2. `Restart-Service MongoDB` (PowerShell chạy quyền admin).
3. `python -c "import pymongo; pymongo.MongoClient(directConnection=True).admin.command('replSetInitiate')"`

Dữ liệu cũ (ví dụ `airline-db`) vẫn được giữ nguyên. Lưu ý: replica set ghi thêm oplog, tương tự binlog mà MySQL 8 bật mặc định.

Đây cũng là một điểm khác biệt đáng đưa vào báo cáo. Nhờ dữ liệu được nhúng, khai sinh (KB3.1) và xóa đăng ký (KB3.4) trên MongoDB chỉ là **1 lệnh nguyên tử trên 1 document**, không cần khóa `SELECT ... FOR UPDATE`. Ngược lại, chuyển hộ khẩu (KB3.2) và tách hộ (KB3.3) sửa nhiều document, nên vẫn cần transaction như MySQL.

### Kết quả và báo cáo

- **[a5/BAO_CAO_SO_SANH.md](a5/BAO_CAO_SO_SANH.md)**: báo cáo nhận xét. Gồm các tiêu chí so sánh rút ra từ bài giảng (C_1–C_8), bảng kết quả, nhận xét, kết luận và hạn chế.
- `a5/results/a5_report.pdf` và `a5/results/img/*.png`: ảnh để chèn vào báo cáo, gồm bảng tổng hợp, biểu đồ, kế hoạch thực thi, output console và lệnh MySQL/MongoDB đặt cạnh nhau. Tạo lại bằng `python a5/bench/report.py`.
- `python a5/mysql/load_mysql.py`: đo thời gian nạp KB1.1 cho MySQL. Script nạp vào database tạm `ipums_vn2019_load` rồi xóa, nên không đụng tới dữ liệu đang dùng.
- `python a5/bench/index_choice.py`: thử nghiệm bổ sung, kiểm tra bộ tối ưu của mỗi hệ có chọn đúng index không.
- Mật khẩu MySQL có thể đặt trong `a5/.env` (`MYSQL_PASSWORD=...`). File này đã nằm trong `.gitignore`.
- Lần đo trong báo cáo đặt **cùng 2 GB bộ nhớ đệm** cho hai hệ, bằng lệnh lúc chạy (không lưu vào file cấu hình):
  `SET GLOBAL innodb_buffer_pool_size = 2147483648;` (MySQL) và
  `db.adminCommand({setParameter: 1, wiredTigerEngineRuntimeConfig: "cache_size=2G"})` (MongoDB).
