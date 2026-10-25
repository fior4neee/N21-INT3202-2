-- Query 1: Point lookup - one household with all of its members.
-- SERIAL is the primary key of hokhau and the prefix of nhankhau's primary key,
-- so this query is always indexed (no "before index" run is needed).
USE ipums_vn2019;

-- Change this to look up a different household.
SET @serial = 6000;

SELECT h.*, n.*
FROM hokhau h
JOIN nhankhau n ON n.SERIAL = h.SERIAL
WHERE h.SERIAL = @serial
ORDER BY n.PERNUM;
