-- 示例业务库（脱敏演示，非生产 DDL）

CREATE TABLE IF NOT EXISTS demo_customers (
  cust_id   VARCHAR(32) PRIMARY KEY COMMENT '客户编号',
  name      VARCHAR(64) COMMENT '客户姓名',
  age       INT COMMENT '年龄',
  sex       VARCHAR(8) COMMENT '性别',
  city      VARCHAR(64) COMMENT '城市'
);

CREATE TABLE IF NOT EXISTS demo_products (
  product_id   VARCHAR(32) PRIMARY KEY COMMENT '产品编号',
  product_name VARCHAR(128) COMMENT '产品名称',
  unit_price   DECIMAL(18,2) COMMENT '单价'
);

CREATE TABLE IF NOT EXISTS demo_orders (
  order_id    VARCHAR(32) PRIMARY KEY COMMENT '订单编号',
  cust_id     VARCHAR(32) COMMENT '客户编号',
  product_id  VARCHAR(32) COMMENT '产品编号',
  amount      DECIMAL(18,2) COMMENT '订单金额',
  order_date  DATE COMMENT '下单日期'
);
