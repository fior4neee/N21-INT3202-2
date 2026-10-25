-- Query 4: Execution plans before and after adding indexes on AGE and GEO1_VN.
-- EXPLAIN ANALYZE (MySQL 8.0.18+) actually runs each query and reports real time
-- and row counts, comparable to MongoDB's .explain("executionStats").
-- Safe to re-run: the indexes are dropped first if they already exist.
USE ipums_vn2019;

SET @serial = 6000;
SET @province = 704001;

-- ---------------------------------------------------------------------------
-- Step 1: remove the indexes (MySQL has no DROP INDEX IF EXISTS).
-- ---------------------------------------------------------------------------
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
   WHERE TABLE_SCHEMA = 'ipums_vn2019' AND TABLE_NAME = 'hokhau'
     AND INDEX_NAME = 'idx_hokhau_geo1_vn') > 0,
  'DROP INDEX idx_hokhau_geo1_vn ON hokhau', 'DO 0');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
   WHERE TABLE_SCHEMA = 'ipums_vn2019' AND TABLE_NAME = 'nhankhau'
     AND INDEX_NAME = 'idx_nhankhau_age') > 0,
  'DROP INDEX idx_nhankhau_age ON nhankhau', 'DO 0');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------------------
-- Step 2: plans WITHOUT the indexes (expect full table scans for Q2 and Q3).
-- Q1 still uses the primary key on SERIAL.
-- ---------------------------------------------------------------------------
EXPLAIN ANALYZE
SELECT h.*, n.*
FROM hokhau h
JOIN nhankhau n ON n.SERIAL = h.SERIAL
WHERE h.SERIAL = @serial
ORDER BY n.PERNUM;

EXPLAIN ANALYZE
SELECT AGE, COUNT(*) AS so_nguoi_co_viec_lam
FROM nhankhau
WHERE EMPSTAT = 1
GROUP BY AGE
ORDER BY AGE;

EXPLAIN ANALYZE
SELECT n.*, h.GEO1_VN
FROM hokhau h
JOIN nhankhau n ON n.SERIAL = h.SERIAL
WHERE h.GEO1_VN = @province
  AND n.AGE BETWEEN 18 AND 60;

-- ---------------------------------------------------------------------------
-- Step 3: add B-Tree indexes. MySQL cannot index columns from two tables
-- together, so GEO1_VN and AGE get separate indexes.
-- ---------------------------------------------------------------------------
CREATE INDEX idx_hokhau_geo1_vn ON hokhau (GEO1_VN);
CREATE INDEX idx_nhankhau_age ON nhankhau (AGE);
ANALYZE TABLE hokhau, nhankhau;

-- ---------------------------------------------------------------------------
-- Step 4: the same plans WITH the indexes.
-- ---------------------------------------------------------------------------
EXPLAIN ANALYZE
SELECT h.*, n.*
FROM hokhau h
JOIN nhankhau n ON n.SERIAL = h.SERIAL
WHERE h.SERIAL = @serial
ORDER BY n.PERNUM;

EXPLAIN ANALYZE
SELECT AGE, COUNT(*) AS so_nguoi_co_viec_lam
FROM nhankhau
WHERE EMPSTAT = 1
GROUP BY AGE
ORDER BY AGE;

EXPLAIN ANALYZE
SELECT n.*, h.GEO1_VN
FROM hokhau h
JOIN nhankhau n ON n.SERIAL = h.SERIAL
WHERE h.GEO1_VN = @province
  AND n.AGE BETWEEN 18 AND 60;

-- Index sizes, for the report.
SELECT TABLE_NAME, INDEX_NAME,
       ROUND(STAT_VALUE * @@innodb_page_size / 1024 / 1024, 1) AS size_mb
FROM mysql.innodb_index_stats
WHERE DATABASE_NAME = 'ipums_vn2019' AND STAT_NAME = 'size'
ORDER BY TABLE_NAME, INDEX_NAME;
