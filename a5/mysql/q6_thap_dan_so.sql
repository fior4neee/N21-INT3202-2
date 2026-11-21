-- Query 6 (KB4.5): tháp dân số - số người theo tỉnh x giới tính x nhóm 5 tuổi (80+ gộp).
-- Quét toàn bộ 2 bảng; SEX: 1 = nam, 2 = nữ.
USE ipums_vn2019;

SELECT h.GEO1_VN, n.SEX, LEAST(FLOOR(n.AGE / 5) * 5, 80) AS nhom_tuoi, COUNT(*) AS so_nguoi
FROM hokhau h
JOIN nhankhau n ON n.SERIAL = h.SERIAL
GROUP BY h.GEO1_VN, n.SEX, nhom_tuoi
ORDER BY h.GEO1_VN, n.SEX, nhom_tuoi;
