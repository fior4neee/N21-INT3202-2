-- KB0: chuẩn bị cho các kịch bản nghiệp vụ (chạy sau hokhau.sql và nhankhau.sql).
-- 1) Bảng biendong_cutru: sổ ghi biến động cư trú (khai sinh, chuyển đi/đến, tách hộ, ...).
-- 2) Các index dùng chung với MongoDB (load_mongo.py tạo đúng các index tương ứng).
USE ipums_vn2019;

CREATE TABLE IF NOT EXISTS biendong_cutru (
  `ID` BIGINT NOT NULL AUTO_INCREMENT,
  `SERIAL` BIGINT NOT NULL,              -- hộ có biến động
  `PERNUM` INT NULL,                     -- nhân khẩu liên quan (NULL nếu cả hộ)
  `LOAI` ENUM('DANG_KY_HO_MOI', 'KHAI_SINH', 'CHUYEN_DI', 'CHUYEN_DEN',
              'TACH_HO', 'XOA_DANG_KY') NOT NULL,
  `SERIAL_LIEN_QUAN` BIGINT NULL,        -- hộ đi/đến hoặc hộ gốc khi tách hộ
  `NGAY` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`ID`),
  KEY `idx_serial` (`SERIAL`)
) ENGINE=InnoDB
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- Index (safe to re-run): GEO1_VN, AGE như q4; MIGRATE5 cho báo cáo nhập cư q5.
DROP PROCEDURE IF EXISTS tao_index;
DELIMITER //
CREATE PROCEDURE tao_index(IN bang VARCHAR(64), IN ten VARCHAR(64), IN cot VARCHAR(64))
BEGIN
  IF (SELECT COUNT(*) FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = bang AND INDEX_NAME = ten) = 0 THEN
    SET @sql = CONCAT('CREATE INDEX ', ten, ' ON ', bang, ' (', cot, ')');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
  END IF;
END //
DELIMITER ;

CALL tao_index('hokhau', 'idx_hokhau_geo1_vn', 'GEO1_VN');
CALL tao_index('nhankhau', 'idx_nhankhau_age', 'AGE');
CALL tao_index('nhankhau', 'idx_nhankhau_migrate5', 'MIGRATE5');
DROP PROCEDURE tao_index;
ANALYZE TABLE hokhau, nhankhau;
