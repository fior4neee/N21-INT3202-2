-- Query 2: Aggregation - number of employed people at each age.
-- EMPSTAT codes (IPUMS): 0 = NIU, 1 = employed, 2 = unemployed, 3 = inactive.
-- Reads the whole nhankhau table, so the AGE / GEO1_VN indexes do not help much here.
USE ipums_vn2019;

SELECT AGE, COUNT(*) AS so_nguoi_co_viec_lam
FROM nhankhau
WHERE EMPSTAT = 1
GROUP BY AGE
ORDER BY AGE;
