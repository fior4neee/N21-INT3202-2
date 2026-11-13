-- =====================================================================
-- Sakila database - full schema (tables, PK, FK, NOT NULL, views,
-- routines, triggers). Import / run in MySQL, then in MySQL Workbench:
-- Database > Reverse Engineer... > choose schema "sakila" -> EER diagram
-- =====================================================================

SET FOREIGN_KEY_CHECKS = 0;

DROP SCHEMA IF EXISTS sakila;
CREATE SCHEMA sakila DEFAULT CHARACTER SET utf8mb4;
USE sakila;

-- ===================== CUSTOMER DATA =====================

CREATE TABLE country (
  country_id   SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  country      VARCHAR(50)       NOT NULL,
  last_update  TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (country_id)
) ENGINE=InnoDB;

CREATE TABLE city (
  city_id      SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  city         VARCHAR(50)       NOT NULL,
  country_id   SMALLINT UNSIGNED NOT NULL,
  last_update  TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (city_id),
  KEY idx_fk_country_id (country_id),
  CONSTRAINT fk_city_country FOREIGN KEY (country_id)
    REFERENCES country (country_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB;

CREATE TABLE address (
  address_id   SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  address      VARCHAR(50)       NOT NULL,
  address2     VARCHAR(50)       DEFAULT NULL,
  district     VARCHAR(20)       NOT NULL,
  city_id      SMALLINT UNSIGNED NOT NULL,
  postal_code  VARCHAR(10)       DEFAULT NULL,
  phone        VARCHAR(20)       NOT NULL,
  last_update  TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (address_id),
  KEY idx_fk_city_id (city_id),
  CONSTRAINT fk_address_city FOREIGN KEY (city_id)
    REFERENCES city (city_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB;

CREATE TABLE customer (
  customer_id  SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  store_id     TINYINT UNSIGNED  NOT NULL,
  first_name   VARCHAR(45)       NOT NULL,
  last_name    VARCHAR(45)       NOT NULL,
  email        VARCHAR(50)       DEFAULT NULL,
  address_id   SMALLINT UNSIGNED NOT NULL,
  active       BOOLEAN           NOT NULL DEFAULT TRUE,
  create_date  DATETIME          NOT NULL,
  last_update  TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (customer_id),
  KEY idx_fk_store_id (store_id),
  KEY idx_fk_address_id (address_id),
  KEY idx_last_name (last_name),
  CONSTRAINT fk_customer_address FOREIGN KEY (address_id)
    REFERENCES address (address_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_customer_store FOREIGN KEY (store_id)
    REFERENCES store (store_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB;

-- ===================== INVENTORY (MOVIE DATABASE) =====================

CREATE TABLE language (
  language_id  TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name         CHAR(20)         NOT NULL,
  last_update  TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (language_id)
) ENGINE=InnoDB;

CREATE TABLE category (
  category_id  TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name         VARCHAR(25)      NOT NULL,
  last_update  TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (category_id)
) ENGINE=InnoDB;

CREATE TABLE actor (
  actor_id     SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  first_name   VARCHAR(45)       NOT NULL,
  last_name    VARCHAR(45)       NOT NULL,
  last_update  TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (actor_id),
  KEY idx_actor_last_name (last_name)
) ENGINE=InnoDB;

CREATE TABLE film (
  film_id              SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  title                VARCHAR(255)      NOT NULL,
  description          TEXT              DEFAULT NULL,
  release_year         YEAR              DEFAULT NULL,
  language_id          TINYINT UNSIGNED  NOT NULL,
  original_language_id TINYINT UNSIGNED  DEFAULT NULL,
  rental_duration      TINYINT UNSIGNED  NOT NULL DEFAULT 3,
  rental_rate          DECIMAL(4,2)      NOT NULL DEFAULT 4.99,
  length               SMALLINT UNSIGNED DEFAULT NULL,
  replacement_cost     DECIMAL(5,2)      NOT NULL DEFAULT 19.99,
  rating               ENUM('G','PG','PG-13','R','NC-17') DEFAULT 'G',
  special_features     SET('Trailers','Commentaries','Deleted Scenes','Behind the Scenes') DEFAULT NULL,
  last_update          TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (film_id),
  KEY idx_title (title),
  KEY idx_fk_language_id (language_id),
  KEY idx_fk_original_language_id (original_language_id),
  CONSTRAINT fk_film_language FOREIGN KEY (language_id)
    REFERENCES language (language_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_film_language_original FOREIGN KEY (original_language_id)
    REFERENCES language (language_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB;

CREATE TABLE film_actor (
  actor_id     SMALLINT UNSIGNED NOT NULL,
  film_id      SMALLINT UNSIGNED NOT NULL,
  last_update  TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (actor_id, film_id),
  KEY idx_fk_film_id (film_id),
  CONSTRAINT fk_film_actor_actor FOREIGN KEY (actor_id)
    REFERENCES actor (actor_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_film_actor_film FOREIGN KEY (film_id)
    REFERENCES film (film_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB;

CREATE TABLE film_category (
  film_id      SMALLINT UNSIGNED NOT NULL,
  category_id  TINYINT UNSIGNED  NOT NULL,
  last_update  TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (film_id, category_id),
  KEY fk_film_category_category (category_id),
  CONSTRAINT fk_film_category_film FOREIGN KEY (film_id)
    REFERENCES film (film_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_film_category_category FOREIGN KEY (category_id)
    REFERENCES category (category_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB;

-- film_text has no FK in Sakila: it is kept in sync with film by triggers
CREATE TABLE film_text (
  film_id      SMALLINT     NOT NULL,
  title        VARCHAR(255) NOT NULL,
  description  TEXT,
  PRIMARY KEY (film_id),
  FULLTEXT KEY idx_title_description (title, description)
) ENGINE=InnoDB;

CREATE TABLE inventory (
  inventory_id MEDIUMINT UNSIGNED NOT NULL AUTO_INCREMENT,
  film_id      SMALLINT UNSIGNED  NOT NULL,
  store_id     TINYINT UNSIGNED   NOT NULL,
  last_update  TIMESTAMP          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (inventory_id),
  KEY idx_fk_film_id (film_id),
  KEY idx_store_id_film_id (store_id, film_id),
  CONSTRAINT fk_inventory_store FOREIGN KEY (store_id)
    REFERENCES store (store_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_inventory_film FOREIGN KEY (film_id)
    REFERENCES film (film_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB;

-- ===================== BUSINESS =====================

CREATE TABLE staff (
  staff_id     TINYINT UNSIGNED  NOT NULL AUTO_INCREMENT,
  first_name   VARCHAR(45)       NOT NULL,
  last_name    VARCHAR(45)       NOT NULL,
  address_id   SMALLINT UNSIGNED NOT NULL,
  picture      BLOB              DEFAULT NULL,
  email        VARCHAR(50)       DEFAULT NULL,
  store_id     TINYINT UNSIGNED  NOT NULL,
  active       BOOLEAN           NOT NULL DEFAULT TRUE,
  username     VARCHAR(16)       NOT NULL,
  password     VARCHAR(40)       DEFAULT NULL,
  last_update  TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (staff_id),
  KEY idx_fk_store_id (store_id),
  KEY idx_fk_address_id (address_id),
  CONSTRAINT fk_staff_store FOREIGN KEY (store_id)
    REFERENCES store (store_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_staff_address FOREIGN KEY (address_id)
    REFERENCES address (address_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB;

CREATE TABLE store (
  store_id         TINYINT UNSIGNED  NOT NULL AUTO_INCREMENT,
  manager_staff_id TINYINT UNSIGNED  NOT NULL,
  address_id       SMALLINT UNSIGNED NOT NULL,
  last_update      TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (store_id),
  UNIQUE KEY idx_unique_manager (manager_staff_id),
  KEY idx_fk_address_id (address_id),
  CONSTRAINT fk_store_staff FOREIGN KEY (manager_staff_id)
    REFERENCES staff (staff_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_store_address FOREIGN KEY (address_id)
    REFERENCES address (address_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB;

CREATE TABLE rental (
  rental_id    INT                NOT NULL AUTO_INCREMENT,
  rental_date  DATETIME           NOT NULL,
  inventory_id MEDIUMINT UNSIGNED NOT NULL,
  customer_id  SMALLINT UNSIGNED  NOT NULL,
  return_date  DATETIME           DEFAULT NULL,
  staff_id     TINYINT UNSIGNED   NOT NULL,
  last_update  TIMESTAMP          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (rental_id),
  UNIQUE KEY (rental_date, inventory_id, customer_id),
  KEY idx_fk_inventory_id (inventory_id),
  KEY idx_fk_customer_id (customer_id),
  KEY idx_fk_staff_id (staff_id),
  CONSTRAINT fk_rental_staff FOREIGN KEY (staff_id)
    REFERENCES staff (staff_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_rental_inventory FOREIGN KEY (inventory_id)
    REFERENCES inventory (inventory_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_rental_customer FOREIGN KEY (customer_id)
    REFERENCES customer (customer_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB;

CREATE TABLE payment (
  payment_id   SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  customer_id  SMALLINT UNSIGNED NOT NULL,
  staff_id     TINYINT UNSIGNED  NOT NULL,
  rental_id    INT               DEFAULT NULL,
  amount       DECIMAL(5,2)      NOT NULL,
  payment_date DATETIME          NOT NULL,
  last_update  TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (payment_id),
  KEY idx_fk_staff_id (staff_id),
  KEY idx_fk_customer_id (customer_id),
  KEY fk_payment_rental (rental_id),
  CONSTRAINT fk_payment_rental FOREIGN KEY (rental_id)
    REFERENCES rental (rental_id) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_payment_customer FOREIGN KEY (customer_id)
    REFERENCES customer (customer_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_payment_staff FOREIGN KEY (staff_id)
    REFERENCES staff (staff_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB;

SET FOREIGN_KEY_CHECKS = 1;

-- ===================== VIEWS =====================

CREATE VIEW customer_list AS
SELECT cu.customer_id AS ID,
       CONCAT(cu.first_name, ' ', cu.last_name) AS name,
       a.address, a.postal_code AS `zip code`, a.phone,
       city.city, country.country,
       IF(cu.active, 'active', '') AS notes,
       cu.store_id AS SID
FROM customer cu
JOIN address a   ON cu.address_id = a.address_id
JOIN city        ON a.city_id = city.city_id
JOIN country     ON city.country_id = country.country_id;

CREATE VIEW film_list AS
SELECT film.film_id AS FID, film.title, film.description,
       category.name AS category, film.rental_rate AS price,
       film.length, film.rating,
       GROUP_CONCAT(CONCAT(actor.first_name, ' ', actor.last_name) SEPARATOR ', ') AS actors
FROM film
LEFT JOIN film_category ON film_category.film_id = film.film_id
LEFT JOIN category      ON category.category_id = film_category.category_id
LEFT JOIN film_actor    ON film.film_id = film_actor.film_id
LEFT JOIN actor         ON film_actor.actor_id = actor.actor_id
GROUP BY film.film_id, category.name;

CREATE VIEW nicer_but_slower_film_list AS
SELECT film.film_id AS FID, film.title, film.description,
       category.name AS category, film.rental_rate AS price,
       film.length, film.rating,
       GROUP_CONCAT(CONCAT(
         UPPER(SUBSTRING(actor.first_name, 1, 1)), LOWER(SUBSTRING(actor.first_name, 2)), ' ',
         UPPER(SUBSTRING(actor.last_name, 1, 1)),  LOWER(SUBSTRING(actor.last_name, 2))
       ) SEPARATOR ', ') AS actors
FROM film
LEFT JOIN film_category ON film_category.film_id = film.film_id
LEFT JOIN category      ON category.category_id = film_category.category_id
LEFT JOIN film_actor    ON film.film_id = film_actor.film_id
LEFT JOIN actor         ON film_actor.actor_id = actor.actor_id
GROUP BY film.film_id, category.name;

CREATE VIEW staff_list AS
SELECT s.staff_id AS ID,
       CONCAT(s.first_name, ' ', s.last_name) AS name,
       a.address, a.postal_code AS `zip code`, a.phone,
       city.city, country.country, s.store_id AS SID
FROM staff s
JOIN address a ON s.address_id = a.address_id
JOIN city      ON a.city_id = city.city_id
JOIN country   ON city.country_id = country.country_id;

CREATE VIEW sales_by_store AS
SELECT CONCAT(c.city, ',', cy.country) AS store,
       CONCAT(m.first_name, ' ', m.last_name) AS manager,
       SUM(p.amount) AS total_sales
FROM payment p
JOIN rental r     ON p.rental_id = r.rental_id
JOIN inventory i  ON r.inventory_id = i.inventory_id
JOIN store s      ON i.store_id = s.store_id
JOIN address a    ON s.address_id = a.address_id
JOIN city c       ON a.city_id = c.city_id
JOIN country cy   ON c.country_id = cy.country_id
JOIN staff m      ON s.manager_staff_id = m.staff_id
GROUP BY s.store_id
ORDER BY cy.country, c.city;

CREATE VIEW sales_by_film_category AS
SELECT c.name AS category, SUM(p.amount) AS total_sales
FROM payment p
JOIN rental r         ON p.rental_id = r.rental_id
JOIN inventory i      ON r.inventory_id = i.inventory_id
JOIN film f           ON i.film_id = f.film_id
JOIN film_category fc ON f.film_id = fc.film_id
JOIN category c       ON fc.category_id = c.category_id
GROUP BY c.name
ORDER BY total_sales DESC;

CREATE VIEW actor_info AS
SELECT a.actor_id, a.first_name, a.last_name,
       GROUP_CONCAT(DISTINCT CONCAT(c.name, ': ', f.title)
                    ORDER BY c.name, f.title SEPARATOR '; ') AS film_info
FROM actor a
LEFT JOIN film_actor fa    ON a.actor_id = fa.actor_id
LEFT JOIN film f           ON fa.film_id = f.film_id
LEFT JOIN film_category fc ON f.film_id = fc.film_id
LEFT JOIN category c       ON fc.category_id = c.category_id
GROUP BY a.actor_id, a.first_name, a.last_name;

-- ===================== ROUTINES & TRIGGERS =====================

DELIMITER $$

CREATE FUNCTION inventory_in_stock(p_inventory_id INT) RETURNS BOOLEAN
READS SQL DATA
BEGIN
  DECLARE v_rentals INT;
  DECLARE v_out INT;

  SELECT COUNT(*) INTO v_rentals FROM rental WHERE inventory_id = p_inventory_id;
  IF v_rentals = 0 THEN
    RETURN TRUE;
  END IF;

  SELECT COUNT(rental_id) INTO v_out
  FROM inventory LEFT JOIN rental USING (inventory_id)
  WHERE inventory.inventory_id = p_inventory_id AND rental.return_date IS NULL;

  IF v_out > 0 THEN
    RETURN FALSE;
  ELSE
    RETURN TRUE;
  END IF;
END$$

CREATE FUNCTION inventory_held_by_customer(p_inventory_id INT) RETURNS INT
READS SQL DATA
BEGIN
  DECLARE v_customer_id INT;
  DECLARE EXIT HANDLER FOR NOT FOUND RETURN NULL;

  SELECT customer_id INTO v_customer_id
  FROM rental
  WHERE return_date IS NULL AND inventory_id = p_inventory_id;

  RETURN v_customer_id;
END$$

CREATE FUNCTION get_customer_balance(p_customer_id INT, p_effective_date DATETIME)
RETURNS DECIMAL(5,2)
READS SQL DATA
BEGIN
  DECLARE v_rentfees DECIMAL(5,2);
  DECLARE v_overfees INT;
  DECLARE v_payments DECIMAL(5,2);

  SELECT IFNULL(SUM(film.rental_rate), 0) INTO v_rentfees
  FROM film, inventory, rental
  WHERE film.film_id = inventory.film_id
    AND inventory.inventory_id = rental.inventory_id
    AND rental.rental_date <= p_effective_date
    AND rental.customer_id = p_customer_id;

  SELECT IFNULL(SUM(IF((TO_DAYS(rental.return_date) - TO_DAYS(rental.rental_date)) > film.rental_duration,
                       (TO_DAYS(rental.return_date) - TO_DAYS(rental.rental_date)) - film.rental_duration, 0)), 0)
  INTO v_overfees
  FROM rental, inventory, film
  WHERE film.film_id = inventory.film_id
    AND inventory.inventory_id = rental.inventory_id
    AND rental.rental_date <= p_effective_date
    AND rental.customer_id = p_customer_id;

  SELECT IFNULL(SUM(payment.amount), 0) INTO v_payments
  FROM payment
  WHERE payment.payment_date <= p_effective_date
    AND payment.customer_id = p_customer_id;

  RETURN v_rentfees + v_overfees - v_payments;
END$$

CREATE PROCEDURE film_in_stock(IN p_film_id INT, IN p_store_id INT, OUT p_film_count INT)
READS SQL DATA
BEGIN
  SELECT inventory_id FROM inventory
  WHERE film_id = p_film_id AND store_id = p_store_id AND inventory_in_stock(inventory_id);

  SELECT COUNT(*) INTO p_film_count FROM inventory
  WHERE film_id = p_film_id AND store_id = p_store_id AND inventory_in_stock(inventory_id);
END$$

CREATE PROCEDURE film_not_in_stock(IN p_film_id INT, IN p_store_id INT, OUT p_film_count INT)
READS SQL DATA
BEGIN
  SELECT inventory_id FROM inventory
  WHERE film_id = p_film_id AND store_id = p_store_id AND NOT inventory_in_stock(inventory_id);

  SELECT COUNT(*) INTO p_film_count FROM inventory
  WHERE film_id = p_film_id AND store_id = p_store_id AND NOT inventory_in_stock(inventory_id);
END$$

CREATE PROCEDURE rewards_report(
  IN  min_monthly_purchases       TINYINT UNSIGNED,
  IN  min_dollar_amount_purchased DECIMAL(10,2),
  OUT count_rewardees             INT)
READS SQL DATA
proc: BEGIN
  DECLARE last_month_start DATE;
  DECLARE last_month_end   DATE;

  IF min_monthly_purchases = 0 THEN
    SELECT 'Minimum monthly purchases parameter must be > 0';
    LEAVE proc;
  END IF;
  IF min_dollar_amount_purchased = 0.00 THEN
    SELECT 'Minimum monthly dollar amount purchased parameter must be > $0.00';
    LEAVE proc;
  END IF;

  SET last_month_start = DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH);
  SET last_month_start = STR_TO_DATE(CONCAT(YEAR(last_month_start), '-', MONTH(last_month_start), '-01'), '%Y-%m-%d');
  SET last_month_end   = LAST_DAY(last_month_start);

  CREATE TEMPORARY TABLE tmpCustomer (customer_id SMALLINT UNSIGNED NOT NULL PRIMARY KEY);

  INSERT INTO tmpCustomer (customer_id)
  SELECT p.customer_id
  FROM payment p
  WHERE DATE(p.payment_date) BETWEEN last_month_start AND last_month_end
  GROUP BY p.customer_id
  HAVING SUM(p.amount) > min_dollar_amount_purchased
     AND COUNT(p.customer_id) > min_monthly_purchases;

  SELECT COUNT(*) FROM tmpCustomer INTO count_rewardees;

  SELECT c.* FROM tmpCustomer t JOIN customer c ON t.customer_id = c.customer_id;

  DROP TEMPORARY TABLE tmpCustomer;
END$$

-- Triggers on film keep film_text in sync
CREATE TRIGGER ins_film AFTER INSERT ON film FOR EACH ROW
BEGIN
  INSERT INTO film_text (film_id, title, description)
  VALUES (NEW.film_id, NEW.title, NEW.description);
END$$

CREATE TRIGGER upd_film AFTER UPDATE ON film FOR EACH ROW
BEGIN
  IF (OLD.title <> NEW.title) OR (OLD.description <> NEW.description)
     OR (OLD.film_id <> NEW.film_id) THEN
    UPDATE film_text
    SET title = NEW.title, description = NEW.description, film_id = NEW.film_id
    WHERE film_id = OLD.film_id;
  END IF;
END$$

CREATE TRIGGER del_film AFTER DELETE ON film FOR EACH ROW
BEGIN
  DELETE FROM film_text WHERE film_id = OLD.film_id;
END$$

DELIMITER ;