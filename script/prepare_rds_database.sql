-- RDS PostgreSQL 业务库准备脚本。
-- 用途：当应用账号只能连接数据库、但无法创建表时，用高权限账号先执行本脚本。
-- 注意：不要在本文件写入任何真实密码。

-- 1. 如业务库尚不存在，先用高权限账号创建业务库。
--    如果控制台已创建 stars_page 数据库，可以跳过这一句。
CREATE DATABASE stars_page OWNER stars_page_demo;

-- 2. 连接到 stars_page 数据库后执行以下授权。
--    psql 可使用：\c stars_page
GRANT CONNECT ON DATABASE stars_page TO stars_page_demo;
GRANT USAGE, CREATE ON SCHEMA public TO stars_page_demo;

-- 3. 允许应用账号使用后续创建的对象。
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO stars_page_demo;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO stars_page_demo;
