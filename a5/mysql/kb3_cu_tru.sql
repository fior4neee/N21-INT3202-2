-- KB3 Đăng ký / quản lý cư trú. Mỗi nghiệp vụ là 1 transaction và ghi vào biendong_cutru.
-- MongoDB tương ứng: a5/mongodb/kb3_cu_tru.js. Đo hiệu năng: a5/bench/bench.py --only KB3
-- Chạy kb1_dang_ky_ho_moi.sql trước để có hộ thử nghiệm @a; script tạo thêm hộ @b.
USE ipums_vn2019;

SET @a = 900000000001;   -- hộ tạo ở kb1_dang_ky_ho_moi.sql
SET @b = 900000000002;   -- hộ nhận người chuyển đến
SET @c = 900000000003;   -- hộ mới khi tách hộ

-- Xóa @b, @c của lần chạy trước (hộ @a do kb1 tạo lại).
DELETE FROM nhankhau WHERE SERIAL IN (@b, @c);
DELETE FROM hokhau WHERE SERIAL IN (@b, @c);

-- Hộ @b: bản sao 1 hộ thật 2 người, ưu tiên ở tỉnh khác hộ @a, để làm nơi chuyển đến.
SET @mau = COALESCE(
  (SELECT MIN(SERIAL) FROM hokhau WHERE PERSONS = 2 AND SERIAL < 900000000000
     AND GEO1_VN <> (SELECT GEO1_VN FROM hokhau WHERE SERIAL = @a)),
  (SELECT MIN(SERIAL) FROM hokhau WHERE PERSONS = 2 AND SERIAL < 900000000000));
INSERT INTO hokhau
SELECT `COUNTRY`, `YEAR`, `SAMPLE`, @b, `PERSONS`, `SUBSAMP`, `ENUMOP`, `FORMTYPE`, `ENUMTYPE`,
       `ENUMMO`, `ENUMHH`, `SAMPMETH`, `SAMPFRCT`, `SAMPUNIT`, `SAMPDRW`, `URBAN`, `REGIONW`,
       `GEOLEV1`, `GEOLEV2`, `POPDENSGEO1`, `POPDENSGEO2`, `AREAMOLLWGEO1`, `AREAMOLLWGEO2`,
       `GEO1_VN`, `GEO1_VN2019`, `GEO2_VN`, `GEO2_VN2019`, `REGNVN`, `OWNERSHIP`, `OWNERSHIPD`
FROM hokhau WHERE SERIAL = @mau;
INSERT INTO nhankhau SELECT @b, PERNUM, PERWT, RELATE, RELATED, AGE, SEX, MARST, MARSTD, AGEMARR,
       MARRYR, BIRTHYR, BTHCERT, RELIGION, RELIGIOND, ETHNICVN, SCHOOL, LIT, EDATTAIN, EDATTAIND,
       YRSCHOOL, EDUCVN, EMPSTAT, EMPSTATD, LABFORCE, CLASSWK, CLASSWKD, WRKAVAIL, MIGRATE5,
       GEOMIG1_5, MIGCAUSE
FROM nhankhau WHERE SERIAL = @mau;

-- ---------------------------------------------------------------------------
-- KB3.1 Khai sinh: thêm trẻ sơ sinh vào hộ @a.
-- ---------------------------------------------------------------------------
START TRANSACTION;
SELECT PERSONS FROM hokhau WHERE SERIAL = @a FOR UPDATE;          -- khóa hộ
SET @pernum = (SELECT COALESCE(MAX(PERNUM), 0) + 1 FROM nhankhau WHERE SERIAL = @a);
INSERT INTO nhankhau VALUES
 (@a, @pernum, 30.0, 3, 3000, 0, 1, 1, 100, 0, 9999, 2019, 9, 1, 1000, 1, 0, 0, 0, 0, 0, 0, 0, 0, 9, 0, 0, 0, 0, 0, 0);
UPDATE hokhau SET PERSONS = PERSONS + 1 WHERE SERIAL = @a;
INSERT INTO biendong_cutru (SERIAL, PERNUM, LOAI) VALUES (@a, @pernum, 'KHAI_SINH');
COMMIT;

-- ---------------------------------------------------------------------------
-- KB3.2 Chuyển hộ khẩu: chuyển nhân khẩu cuối của hộ @a sang hộ @b (khác tỉnh).
-- ---------------------------------------------------------------------------
START TRANSACTION;
SELECT SERIAL FROM hokhau WHERE SERIAL IN (@a, @b) ORDER BY SERIAL FOR UPDATE;  -- khóa theo thứ tự cố định
SET @p_di  = (SELECT MAX(PERNUM) FROM nhankhau WHERE SERIAL = @a);
SET @p_den = (SELECT COALESCE(MAX(PERNUM), 0) + 1 FROM nhankhau WHERE SERIAL = @b);
INSERT INTO nhankhau SELECT @b, @p_den, PERWT, RELATE, RELATED, AGE, SEX, MARST, MARSTD, AGEMARR,
       MARRYR, BIRTHYR, BTHCERT, RELIGION, RELIGIOND, ETHNICVN, SCHOOL, LIT, EDATTAIN, EDATTAIND,
       YRSCHOOL, EDUCVN, EMPSTAT, EMPSTATD, LABFORCE, CLASSWK, CLASSWKD, WRKAVAIL, MIGRATE5,
       GEOMIG1_5, MIGCAUSE
FROM nhankhau WHERE SERIAL = @a AND PERNUM = @p_di;
DELETE FROM nhankhau WHERE SERIAL = @a AND PERNUM = @p_di;
UPDATE hokhau SET PERSONS = PERSONS - 1 WHERE SERIAL = @a;
UPDATE hokhau SET PERSONS = PERSONS + 1 WHERE SERIAL = @b;
INSERT INTO biendong_cutru (SERIAL, PERNUM, LOAI, SERIAL_LIEN_QUAN) VALUES
 (@a, @p_di, 'CHUYEN_DI', @b),
 (@b, @p_den, 'CHUYEN_DEN', @a);
COMMIT;

-- ---------------------------------------------------------------------------
-- KB3.3 Tách hộ: các thành viên PERNUM >= 2 của hộ @a lập hộ mới @c.
-- ---------------------------------------------------------------------------
START TRANSACTION;
SELECT PERSONS FROM hokhau WHERE SERIAL = @a FOR UPDATE;
SET @k = (SELECT COUNT(*) FROM nhankhau WHERE SERIAL = @a AND PERNUM >= 2);
INSERT INTO hokhau
SELECT `COUNTRY`, `YEAR`, `SAMPLE`, @c, @k, `SUBSAMP`, `ENUMOP`, `FORMTYPE`, `ENUMTYPE`,
       `ENUMMO`, `ENUMHH`, `SAMPMETH`, `SAMPFRCT`, `SAMPUNIT`, `SAMPDRW`, `URBAN`, `REGIONW`,
       `GEOLEV1`, `GEOLEV2`, `POPDENSGEO1`, `POPDENSGEO2`, `AREAMOLLWGEO1`, `AREAMOLLWGEO2`,
       `GEO1_VN`, `GEO1_VN2019`, `GEO2_VN`, `GEO2_VN2019`, `REGNVN`, `OWNERSHIP`, `OWNERSHIPD`
FROM hokhau WHERE SERIAL = @a;
INSERT INTO nhankhau
SELECT @c, ROW_NUMBER() OVER (ORDER BY PERNUM), PERWT, RELATE, RELATED, AGE, SEX, MARST, MARSTD,
       AGEMARR, MARRYR, BIRTHYR, BTHCERT, RELIGION, RELIGIOND, ETHNICVN, SCHOOL, LIT, EDATTAIN,
       EDATTAIND, YRSCHOOL, EDUCVN, EMPSTAT, EMPSTATD, LABFORCE, CLASSWK, CLASSWKD, WRKAVAIL,
       MIGRATE5, GEOMIG1_5, MIGCAUSE
FROM nhankhau WHERE SERIAL = @a AND PERNUM >= 2;
UPDATE nhankhau SET RELATE = 1, RELATED = 1000 WHERE SERIAL = @c AND PERNUM = 1;  -- chủ hộ mới
DELETE FROM nhankhau WHERE SERIAL = @a AND PERNUM >= 2;
UPDATE hokhau SET PERSONS = PERSONS - @k WHERE SERIAL = @a;
INSERT INTO biendong_cutru (SERIAL, PERNUM, LOAI, SERIAL_LIEN_QUAN) VALUES (@c, NULL, 'TACH_HO', @a);
COMMIT;

-- ---------------------------------------------------------------------------
-- KB3.4 Xóa đăng ký thường trú (khai tử / ra nước ngoài): nhân khẩu cuối của hộ @c.
-- ---------------------------------------------------------------------------
START TRANSACTION;
SELECT PERSONS FROM hokhau WHERE SERIAL = @c FOR UPDATE;
SET @p_xoa = (SELECT MAX(PERNUM) FROM nhankhau WHERE SERIAL = @c);
DELETE FROM nhankhau WHERE SERIAL = @c AND PERNUM = @p_xoa;
UPDATE hokhau SET PERSONS = PERSONS - 1 WHERE SERIAL = @c;
INSERT INTO biendong_cutru (SERIAL, PERNUM, LOAI) VALUES (@c, @p_xoa, 'XOA_DANG_KY');
COMMIT;

-- Kết quả: các hộ thử nghiệm và lịch sử biến động
SELECT h.SERIAL, h.GEO1_VN, h.PERSONS, n.PERNUM, n.AGE, n.RELATE
FROM hokhau h JOIN nhankhau n ON n.SERIAL = h.SERIAL
WHERE h.SERIAL >= 900000000000 ORDER BY h.SERIAL, n.PERNUM;
SELECT * FROM biendong_cutru WHERE SERIAL >= 900000000000 ORDER BY ID;

-- DỌN DẸP
-- DELETE FROM nhankhau WHERE SERIAL >= 900000000000;
-- DELETE FROM hokhau WHERE SERIAL >= 900000000000;
-- DELETE FROM biendong_cutru WHERE SERIAL >= 900000000000;
