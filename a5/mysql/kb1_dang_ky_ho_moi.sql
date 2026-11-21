-- KB1.2 Thu thập dữ liệu dân cư: đăng ký 1 hộ mới với các nhân khẩu, trong 1 transaction.
-- MongoDB tương ứng: a5/mongodb/kb1_dang_ky_ho_moi.js. Đo hiệu năng: a5/bench/bench.py --only KB1
-- Hộ thử nghiệm dùng SERIAL >= 900000000000; khối DỌN DẸP ở cuối xóa chúng.
USE ipums_vn2019;

SET @serial = 900000000001;

-- Xóa dữ liệu thử của lần chạy trước để script chạy lại được.
DELETE FROM nhankhau WHERE SERIAL >= 900000000000;
DELETE FROM hokhau WHERE SERIAL >= 900000000000;
DELETE FROM biendong_cutru WHERE SERIAL >= 900000000000;

START TRANSACTION;

-- Hộ mới, địa chỉ/thông tin hộ lấy theo hộ 6000 (cùng tỉnh/huyện).
INSERT INTO hokhau
SELECT `COUNTRY`, `YEAR`, `SAMPLE`, @serial, 3, `SUBSAMP`, `ENUMOP`, `FORMTYPE`, `ENUMTYPE`,
       `ENUMMO`, `ENUMHH`, `SAMPMETH`, `SAMPFRCT`, `SAMPUNIT`, `SAMPDRW`, `URBAN`, `REGIONW`,
       `GEOLEV1`, `GEOLEV2`, `POPDENSGEO1`, `POPDENSGEO2`, `AREAMOLLWGEO1`, `AREAMOLLWGEO2`,
       `GEO1_VN`, `GEO1_VN2019`, `GEO2_VN`, `GEO2_VN2019`, `REGNVN`, `OWNERSHIP`, `OWNERSHIPD`
FROM hokhau WHERE SERIAL = 6000;

-- 3 nhân khẩu (chủ hộ, vợ/chồng, con), các mã còn lại theo IPUMS.
INSERT INTO nhankhau VALUES
 (@serial, 1, 30.0, 1, 1000, 35, 1, 2, 200, 27, 2011, 1984, 9, 1, 1000, 1, 3, 1, 3, 321, 12, 242, 1, 110, 1, 2, 200, 0, 11, 704001, 0),
 (@serial, 2, 30.0, 2, 2000, 32, 2, 2, 200, 24, 2011, 1987, 9, 1, 1000, 1, 3, 1, 3, 321, 12, 242, 1, 110, 1, 2, 200, 0, 11, 704001, 0),
 (@serial, 3, 30.0, 3, 3000,  6, 1, 1, 100,  0, 9999, 2013, 9, 1, 1000, 1, 2, 1, 1, 110,  1, 201, 0,   0, 9, 0,   0, 0, 11, 704001, 0);

INSERT INTO biendong_cutru (SERIAL, PERNUM, LOAI) VALUES (@serial, NULL, 'DANG_KY_HO_MOI');

COMMIT;

-- Kiểm tra
SELECT h.SERIAL, h.PERSONS, h.GEO1_VN, n.PERNUM, n.AGE, n.SEX, n.RELATE
FROM hokhau h JOIN nhankhau n ON n.SERIAL = h.SERIAL WHERE h.SERIAL = @serial;

-- DỌN DẸP (chạy khi muốn xóa dữ liệu thử nghiệm)
-- DELETE FROM nhankhau WHERE SERIAL >= 900000000000;
-- DELETE FROM hokhau WHERE SERIAL >= 900000000000;
-- DELETE FROM biendong_cutru WHERE SERIAL >= 900000000000;
