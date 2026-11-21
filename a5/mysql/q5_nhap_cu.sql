-- Query 5 (KB4.4): người chuyển đến 1 tỉnh trong 5 năm trước điều tra, theo tỉnh nơi đi.
-- MIGRATE5 = 20: chuyển từ tỉnh khác đến; GEOMIG1_5: tỉnh nơi ở 5 năm trước.
-- Dùng idx_hokhau_geo1_vn / idx_nhankhau_migrate5 (tạo ở kb0_chuan_bi.sql).
USE ipums_vn2019;

SET @province = 704001;

SELECT n.GEOMIG1_5 AS tinh_noi_di, COUNT(*) AS so_nguoi
FROM hokhau h
JOIN nhankhau n ON n.SERIAL = h.SERIAL
WHERE h.GEO1_VN = @province
  AND n.MIGRATE5 = 20
GROUP BY n.GEOMIG1_5
ORDER BY so_nguoi DESC, tinh_noi_di;
