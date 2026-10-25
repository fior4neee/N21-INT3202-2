-- Query 3: Range query - working-age people (18-60) in one province.
-- GEO1_VN lives in hokhau and AGE in nhankhau, so MySQL needs a join here.
-- Workbench only shows the first 1000 rows by default; time this from a script
-- (or remove the limit under Query > Limit Rows) to fetch the full result.
USE ipums_vn2019;

SET @province = 704001;

SELECT n.*, h.GEO1_VN
FROM hokhau h
JOIN nhankhau n ON n.SERIAL = h.SERIAL
WHERE h.GEO1_VN = @province
  AND n.AGE BETWEEN 18 AND 60;

-- Row count, to check that MongoDB returns the same number of people.
SELECT COUNT(*) AS so_nguoi
FROM hokhau h
JOIN nhankhau n ON n.SERIAL = h.SERIAL
WHERE h.GEO1_VN = @province
  AND n.AGE BETWEEN 18 AND 60;
