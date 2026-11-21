-- KB2 Cập nhật dữ liệu dân cư.
-- MongoDB tương ứng: a5/mongodb/kb2_cap_nhat.js. Đo hiệu năng: a5/bench/bench.py --only KB2
-- Cả hai khối đều trả dữ liệu về như cũ ở cuối.
USE ipums_vn2019;

-- ---------------------------------------------------------------------------
-- KB2.1 Cập nhật 1 nhân khẩu: đăng ký kết hôn (tìm theo khóa chính SERIAL + PERNUM).
-- ---------------------------------------------------------------------------
SET @serial = 6000;
SET @pernum = 2;

SELECT SERIAL, PERNUM, AGE, MARST, MARSTD, AGEMARR, MARRYR
INTO @s, @p, @age, @old_marst, @old_marstd, @old_agemarr, @old_marryr
FROM nhankhau WHERE SERIAL = @serial AND PERNUM = @pernum;

UPDATE nhankhau
SET MARST = 2, MARSTD = 200, AGEMARR = @age, MARRYR = 2019
WHERE SERIAL = @serial AND PERNUM = @pernum;

SELECT SERIAL, PERNUM, MARST, MARSTD, AGEMARR, MARRYR
FROM nhankhau WHERE SERIAL = @serial AND PERNUM = @pernum;

-- Trả lại giá trị cũ
UPDATE nhankhau
SET MARST = @old_marst, MARSTD = @old_marstd, AGEMARR = @old_agemarr, MARRYR = @old_marryr
WHERE SERIAL = @serial AND PERNUM = @pernum;

-- ---------------------------------------------------------------------------
-- KB2.2 Cập nhật hàng loạt: cập nhật tuổi đầu năm cho toàn bộ dân 1 tỉnh.
-- GEO1_VN nằm ở hokhau, AGE ở nhankhau, nên phải UPDATE ... JOIN.
-- ---------------------------------------------------------------------------
SET @province = 704001;

UPDATE nhankhau n JOIN hokhau h ON n.SERIAL = h.SERIAL
SET n.AGE = n.AGE + 1
WHERE h.GEO1_VN = @province;

-- Trả lại như cũ
UPDATE nhankhau n JOIN hokhau h ON n.SERIAL = h.SERIAL
SET n.AGE = n.AGE - 1
WHERE h.GEO1_VN = @province;
