-- 扫描暂存（用户审核后再写入 table_meta / column_meta）

CREATE TABLE IF NOT EXISTS staging_table_meta (
    table_id        VARCHAR(64)  PRIMARY KEY,
    db_name         VARCHAR(128) NOT NULL,
    table_name      VARCHAR(256) NOT NULL,
    description     TEXT         DEFAULT NULL COMMENT '待审核表说明（必填）',
    hive_comment    TEXT         DEFAULT NULL COMMENT '源库原始表 COMMENT',
    comment_source  VARCHAR(16)  DEFAULT NULL COMMENT 'schema|ddl|llm|manual',
    scanned_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_stg_db_table (db_name, table_name)
) COMMENT='扫描暂存-表';

CREATE TABLE IF NOT EXISTS staging_column_meta (
    column_id       VARCHAR(64)  PRIMARY KEY,
    table_id        VARCHAR(64)  NOT NULL,
    column_name     VARCHAR(256) NOT NULL,
    data_type       VARCHAR(128) NOT NULL,
    description     TEXT         DEFAULT NULL COMMENT '待审核字段说明（必填）',
    hive_comment    TEXT         DEFAULT NULL COMMENT '源库原始字段 COMMENT',
    comment_source  VARCHAR(16)  DEFAULT NULL COMMENT 'schema|ddl|llm|manual',
    ordinal_pos     INT          DEFAULT NULL,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_stg_table_column (table_id, column_name),
    KEY idx_stg_table_id (table_id)
) COMMENT='扫描暂存-字段';
