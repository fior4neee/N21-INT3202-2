# A5: So sánh MongoDB và MySQL cho bài toán quản lý nhân khẩu

Dữ liệu: IPUMS Việt Nam 2019, gồm **1.001.120 hộ** và **3.550.482 nhân khẩu**, nạp cùng một bộ vào hai hệ.
Máy đo: Windows 11, MySQL 8.0.46 (InnoDB) và MongoDB 8.0.3 (WiredTiger), cùng chạy trên một máy, chạy ngày 21/11/2026.
Số liệu gốc nằm trong `a5/results/`; ảnh và PDF nằm trong `a5/results/img/` và `a5/results/a5_report.pdf`.

## 1. Bài giảng gợi ý những tiêu chí so sánh nào

Mình đọc 6 file bài giảng trong `a5/data/` (C_1, C_2_1, C_2_2, C_3, C_4, C_8) và rút ra các tiêu chí dùng được để so hai hệ. Bảng dưới đánh giá từng tiêu chí: có **quan trọng** với nghiệp vụ hộ khẩu/nhân khẩu không, và bài này **đã đo** chưa.

| Tiêu chí (chương) | Ý chính trong slide | Quan trọng với hộ khẩu? | Đã đo? |
|---|---|---|---|
| Mô hình dữ liệu (C_1) | Quan hệ, tài liệu (document), NoSQL | **Có**: hộ–nhân khẩu là quan hệ cha–con 1–n | Có: 2 bảng + JOIN so với 1 document nhúng |
| Engine, lưu trữ, nén (C_2_1) | InnoDB: ACID, khóa mức dòng, khóa ngoại, clustered index, cache, nén | **Có**: dữ liệu dân cư rất lớn | Có: dung lượng sau khi nạp |
| Kiểu dữ liệu (C_2_1) | INT/BIGINT/DECIMAL… | Ít | Giữ nguyên tên và kiểu cột ở cả hai hệ |
| Index B-Tree, index ghép, khi nào index được dùng (C_2_1) | `EXPLAIN` trước/sau khi có index; ghi tốn thêm chi phí; quy tắc tiền tố trái (left-prefix) | **Có** | Có: explain mọi truy vấn đọc, thêm thử nghiệm chọn index |
| JOIN, gom nhóm, truy vấn con (C_2_2) | JOIN, GROUP BY/HAVING | **Có**: mọi báo cáo dân số đều gom nhóm | Có: KB4.1–KB4.5 |
| Full-text, View (C_2_2) | Tìm văn bản | Ít (dữ liệu toàn là mã số) | Không |
| Thủ tục, trigger, event (C_3) | Đặt logic nghiệp vụ trong CSDL | Vừa phải (có thể dùng để ghi lịch sử biến động) | Không đo, chỉ nhận xét ở mục 4 |
| Giao dịch ACID (C_4) | START TRANSACTION/COMMIT/ROLLBACK; ví dụ đặt hàng = thêm cha + con | **Rất quan trọng**: chuyển hộ khẩu, tách hộ phải "hoặc làm hết, hoặc không làm gì" | Một phần: đo thời gian KB1.2, KB3.x; MongoDB chưa có transaction (mục 5) |
| Mức cô lập, đồng thời (C_4) | Dirty read, phantom, khóa | **Quan trọng** khi nhiều cán bộ cùng nhập liệu | **Chưa đo** |
| Xử lý và tối ưu truy vấn (C_8) | Kế hoạch thực thi, chi phí I/O, bộ nhớ đệm (buffer), đẩy phép chọn xuống sớm | **Có** | Có: explain, thời gian server so với client, thử nghiệm chọn index |
| Cấu hình server (C_1, C_8) | `my.ini`, buffer pool, OLTP/OLAP | **Có**: ảnh hưởng mạnh tới số đo | Có: chỉnh hai hệ về cùng 2 GB bộ nhớ đệm |

Vì vậy báo cáo tập trung vào 5 nhóm quan trọng nhất cho bài toán hộ khẩu: **mô hình và lưu trữ**, **nạp dữ liệu**, **tra cứu, thống kê và index**, **cập nhật và nghiệp vụ cư trú (giao dịch)**, **tối ưu truy vấn**.

## 2. Đã so sánh những gì

| Nhóm | Kịch bản | MySQL | MongoDB | Nhanh hơn |
|---|---|---|---|---|
| Thu thập | KB1.1 Nạp toàn bộ dữ liệu | 70,3 s (+ 29,6 s tạo index) | 69,5 s (+ 12,6 s tạo index) | gần bằng; tạo index MongoDB nhanh hơn 2,3 lần |
| | KB1.2 Đăng ký hộ mới (hộ + 4 người + ghi biến động) | 4,49 ms | 3,48 ms | MongoDB ×1,3 |
| Cập nhật | KB2.1 Đăng ký kết hôn (1 người) | 3,27 ms | 1,71 ms | MongoDB ×1,9 |
| | KB2.2 Tăng tuổi toàn bộ dân 1 tỉnh (373 nghìn người) | 13,42 s | 4,50 s | MongoDB ×3,0 |
| Cư trú | KB3.1 Khai sinh | 9,14 ms | 5,56 ms | MongoDB ×1,6 |
| | KB3.2 Chuyển hộ khẩu | 9,37 ms | 9,87 ms | gần bằng |
| | KB3.3 Tách hộ | 8,34 ms | 10,29 ms | MySQL ×1,2 |
| | KB3.4 Xóa đăng ký thường trú | 7,73 ms | 5,80 ms | MongoDB ×1,3 |
| Tra cứu | KB4.1 Tra 1 hộ theo mã | 0,67 ms | 0,26 ms | MongoDB ×2,5 |
| | KB4.2 Số người có việc làm theo tuổi (cả nước) | 8,73 s | 6,45 s | MongoDB ×1,4 |
| | KB4.3 Danh sách người 18–60 tuổi của Hà Nội (216 nghìn dòng) | 4,26 s | 13,65 s | **MySQL ×3,2** |
| | KB4.4 Người nhập cư vào Hà Nội theo tỉnh nơi đi | 227 ms | 99 ms | MongoDB ×2,3 |
| | KB4.5 Tháp dân số theo tỉnh × giới tính × nhóm tuổi | 4,55 s | 3,85 s | MongoDB ×1,2 |

Giá trị trong bảng là median, đo từ phía client và gồm cả thời gian nhận hết kết quả. Cả 5 truy vấn đọc đều cho **kết quả giống hệt nhau** ở hai hệ (`bench.py` tự kiểm tra). Dung lượng ngay sau khi nạp: MySQL 722 MB dữ liệu + 189 MB index; MongoDB 264 MB + 40 MB.

## 3. Nhận xét

**1. Mô hình "sổ hộ khẩu nhúng" của MongoDB hợp với các thao tác trên từng hộ.** Tra cứu một hộ (KB4.1), khai sinh (KB3.1), xóa đăng ký (KB3.4) và đăng ký kết hôn (KB2.1) chỉ đọc hoặc ghi **một document**. MySQL phải JOIN hai bảng, hoặc mở transaction và khóa dòng (`SELECT … FOR UPDATE`) rồi chạy 3–4 lệnh. Vì vậy MongoDB nhanh hơn 1,3–2,5 lần ở các thao tác này. Khai sinh và xóa đăng ký còn **nguyên tử sẵn**, vì là một lệnh cập nhật duy nhất trên một document, nên không cần transaction.

**2. Nghiệp vụ liên quan nhiều hộ thì hai hệ ngang nhau, MySQL nhỉnh hơn.** Chuyển hộ khẩu (KB3.2) và tách hộ (KB3.3) sửa hai document, nên MongoDB phải đọc document ra phía client, xử lý, rồi ghi lại. Trong khi đó MySQL làm được ngay trên server (`INSERT … SELECT`, `ROW_NUMBER()`). Lưu ý quan trọng: MongoDB đo ở chế độ standalone, **không có transaction**, nên đây là trường hợp có lợi cho MongoDB. Có transaction thật (replica set) thì MongoDB sẽ chậm hơn nữa (mục 5). Đây là điểm mạnh của mô hình quan hệ đúng như chương 4 trình bày: giao dịch nhiều bảng là chức năng cốt lõi, có sẵn và đã được tối ưu.

**3. Cập nhật hàng loạt: MongoDB nhanh gấp 3 lần (KB2.2).** Tăng tuổi cho 373 nghìn người ở Hà Nội: MongoDB chỉ sửa 105 nghìn document hộ, mỗi document một lần ghi. MySQL phải `UPDATE … JOIN` rồi ghi 373 nghìn dòng, cập nhật thêm index `AGE` và ghi binlog. Điều này khớp với ý trong chương 2: index giúp đọc nhanh nhưng làm việc ghi tốn thêm chi phí.

**4. Danh sách dài thì MySQL thắng, và lý do nằm ở kế hoạch thực thi (KB4.3).** Theo `EXPLAIN ANALYZE`, MySQL chỉ mất **475 ms trên server**: tìm các hộ của Hà Nội qua `idx_hokhau_geo1_vn`, rồi tra từng hộ qua khóa chính để lấy nhân khẩu. Phần còn lại của 4,26 s là thời gian Python giải mã 216 nghìn dòng. MongoDB mất 9,9 s trên server, vì planner **chọn sai index** (mục 6).

**5. Thống kê quét toàn bộ dữ liệu thì MongoDB nhỉnh hơn một chút (KB4.2, KB4.5).** Cả hai đều phải đọc toàn bộ 3,55 triệu người. MongoDB đọc document hộ (dữ liệu hộ và nhân khẩu nằm sẵn cạnh nhau) rồi `$unwind`. MySQL phải nối 1 triệu hộ với 3,55 triệu người bằng nested loop. Với KB4.4 (nhập cư), cả hai đều dùng index `MIGRATE5`, nhưng MongoDB không cần bước JOIN ngược về bảng hộ nên nhanh hơn 2,3 lần.

**6. Thử nghiệm bổ sung: cả hai bộ tối ưu đều có lúc chọn sai** (`a5/bench/index_choice.py`, đo thời gian trên server).

| Truy vấn | Bộ tối ưu tự chọn | Sửa lại | Kết quả |
|---|---|---|---|
| KB4.3 MongoDB | Index `nhankhau.AGE` (multikey): đọc 914.634 document, mất 11,6 s | `hint` vào `idx_geo1_vn`: đọc 104.769 document | **1,7 s** (nhanh ×6,8) |
| KB4.2 MySQL | Quét toàn bộ index `AGE` rồi tra ngược vào bảng cho từng dòng: 7,7 s | `IGNORE INDEX`, quét thẳng bảng | **1,2 s** (nhanh ×6,2) |
| KB4.2 MySQL | (như trên) | Index ghép `(EMPSTAT, AGE)`, đủ cột để trả lời mà không cần đọc bảng (covering) | **0,56 s** (nhanh ×14) |

Với MongoDB, điều kiện khoảng (`AGE` từ 18 đến 60) trên một trường nằm trong mảng không thu hẹp được phạm vi quét index như với cột thường, vì `$gte` và `$lte` có thể được thỏa bởi hai người khác nhau trong cùng một hộ. Nên dùng `$elemMatch`, chỉ định index (`hint`), hoặc tạo index ghép `{GEO1_VN, nhankhau.AGE}`. Index ghép kiểu này MySQL không làm được, vì hai cột nằm ở hai bảng. Với MySQL, bộ tối ưu chọn index `AGE` để tránh phải sắp xếp cho `GROUP BY AGE`, nhưng cái giá là 3,55 triệu lần truy cập ngẫu nhiên vào bảng. Đây đúng là ý của chương 8: chọn phương án dựa trên ước lượng chi phí, nhưng ước lượng có thể sai, nên **phải đọc kế hoạch thực thi**, không thể chỉ nhìn thời gian chạy.

**7. Nạp dữ liệu và dung lượng.** Thời gian nạp gần như bằng nhau (khoảng 70 s). MongoDB tạo index nhanh hơn, vì index của nó nhỏ hơn (40 MB so với 189 MB). MongoDB chiếm ít đĩa hơn khoảng 3 lần nhờ WiredTiger nén snappy, dù nếu không nén thì dữ liệu BSON lên tới khoảng 1,8 GB, vì tên trường được lặp lại trong từng document. Kích thước InnoDB không ổn định: cùng một bộ dữ liệu nhưng dao động 680–1.040 MB tùy lịch sử của bảng (chạy `OPTIMIZE TABLE` còn làm nó lớn hơn). Vì vậy chỉ nên so sánh theo **bậc độ lớn**.

**8. Ràng buộc toàn vẹn (đánh giá định tính, không đo).** MySQL có khóa ngoại `nhankhau → hokhau`, `NOT NULL` và strict mode, nên không thể tồn tại một nhân khẩu không thuộc hộ nào. Với MongoDB, ràng buộc này được bảo đảm nhờ cấu trúc nhúng (nhân khẩu nằm sẵn trong hộ). Nhưng `PERSONS` phải khớp với số phần tử của mảng, và `biendong_cutru` phải trỏ tới đúng hộ, đều do ứng dụng tự bảo đảm (có thể thêm `$jsonSchema`). Thủ tục, trigger và event (chương 3) cũng chỉ có ở MySQL. MongoDB không có thủ tục lưu sẵn; muốn tự động ghi lịch sử biến động thì dùng change streams hoặc trigger của Atlas.

## 4. Kết luận cho bài toán quản lý nhân khẩu

- **Thao tác trên từng hộ** (tra cứu, khai sinh, kết hôn, xóa đăng ký): MongoDB với mô hình nhúng đơn giản và nhanh hơn rõ rệt (1,3–2,5 lần).
- **Nghiệp vụ liên quan nhiều hộ** (chuyển hộ khẩu, tách hộ) cần ACID: MySQL tự nhiên hơn, ngang hoặc nhanh hơn, và đây là phần nghiệp vụ không được phép sai.
- **Thống kê và báo cáo**: hai hệ cùng bậc thời gian. Yếu tố quyết định là **index và kế hoạch thực thi đúng** (chênh tới 6–14 lần), chứ không phải chọn hệ nào.
- **Lưu trữ**: MongoDB tiết kiệm đĩa khoảng 3 lần nhờ nén.

Nhìn chung không hệ nào thắng tuyệt đối. Với một hệ thống đăng ký cư trú thật, nơi tính đúng đắn của các giao dịch là bắt buộc, MySQL an toàn hơn. MongoDB phù hợp cho phần tra cứu hồ sơ hộ và báo cáo, hoặc khi dữ liệu mỗi hộ có cấu trúc thay đổi.

## 5. Hạn chế và việc nên làm tiếp

1. **MongoDB chưa có transaction.** Service MongoDB trên máy là standalone, nên KB1.2 và KB3.x của MongoDB chạy không có transaction, trong khi MySQL có. Cần chuyển sang replica set 1 node (README mục "Transaction của MongoDB", phải chạy quyền admin) rồi chạy lại `python a5/bench/bench.py --only KB1 KB3`. Dự kiến MongoDB sẽ chậm thêm ở KB3.2 và KB3.3.
2. **Chưa đo truy cập đồng thời** (chương 4: mức cô lập, khóa): ví dụ 10–50 cán bộ cùng đăng ký hoặc chuyển hộ. Đây là tiêu chí quan trọng tiếp theo cho bài toán OLTP này.
3. **Không cân bằng về log ghi:** MySQL bật binlog (mặc định của MySQL 8), MongoDB standalone không có oplog. Điều này có lợi cho MongoDB ở các kịch bản ghi, nhất là KB2.2.
4. **Thời gian đo từ client** gồm cả chi phí giải mã kết quả trong Python (pymysql thuần Python, bson có phần viết bằng C), nên ảnh hưởng tới các truy vấn trả về nhiều dòng như KB4.3. Thời gian thuần trên server nằm trong `results/explain_*/`.
5. **Chỉ một máy, chỉ một lượt đo:** mỗi kịch bản chạy 3–100 lần trong một lượt; chưa lặp lại vào ngày khác. Bộ nhớ đệm của cả hai hệ được đặt bằng nhau (2 GB) bằng lệnh lúc chạy, không lưu vào file cấu hình.

## 6. Cách chạy lại

```powershell
python a5/mysql/load_mysql.py      # KB1.1 MySQL (nạp vào database tạm rồi xóa)
python a5/mongodb/load_mongo.py    # KB1.1 MongoDB
python a5/bench/bench.py --explain # KB1.2-KB4.5
python a5/bench/index_choice.py    # thử nghiệm chọn index
python a5/bench/report.py          # ảnh PNG + PDF
```
