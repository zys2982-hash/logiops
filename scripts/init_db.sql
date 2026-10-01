-- LogiOps 本地数据库初始化（需要 MySQL root 密码）
-- 用法：mysql -uroot -p < scripts/init_db.sql
-- 说明：应用不要用 root 连接；这里创建专用账号 logiops / logiops 与两个 schema。

CREATE DATABASE IF NOT EXISTS `logiops`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE DATABASE IF NOT EXISTS `logiops_test`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

-- MySQL 8 默认 caching_sha2_password；PyMySQL 需安装 cryptography（已在后端依赖中）
CREATE USER IF NOT EXISTS 'logiops'@'localhost' IDENTIFIED BY 'logiops';
CREATE USER IF NOT EXISTS 'logiops'@'127.0.0.1' IDENTIFIED BY 'logiops';
CREATE USER IF NOT EXISTS 'logiops'@'%' IDENTIFIED BY 'logiops';

GRANT ALL PRIVILEGES ON `logiops`.* TO 'logiops'@'localhost';
GRANT ALL PRIVILEGES ON `logiops`.* TO 'logiops'@'127.0.0.1';
GRANT ALL PRIVILEGES ON `logiops`.* TO 'logiops'@'%';
GRANT ALL PRIVILEGES ON `logiops_test`.* TO 'logiops'@'localhost';
GRANT ALL PRIVILEGES ON `logiops_test`.* TO 'logiops'@'127.0.0.1';
GRANT ALL PRIVILEGES ON `logiops_test`.* TO 'logiops'@'%';

FLUSH PRIVILEGES;

SELECT '数据库与账号已就绪' AS result;
