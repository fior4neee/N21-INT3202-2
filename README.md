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