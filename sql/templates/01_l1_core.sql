-- L1：结构化元数据（Schema 真相源）

CREATE TABLE IF NOT EXISTS table_meta (
    table_id        VARCHAR(64)  PRIMARY KEY COMMENT '内部ID，如 T001',
    db_name         VARCHAR(128) NOT NULL COMMENT 'Hive 库名',
    table_name      VARCHAR(256) NOT NULL COMMENT 'Hive 表名',
    cn_name         VARCHAR(256) DEFAULT NULL COMMENT '中文名',
    description     TEXT         DEFAULT NULL COMMENT '业务说明',
    domain          VARCHAR(64)  DEFAULT NULL COMMENT '主题域：用户/交易/流量等',
    owner           VARCHAR(64)  DEFAULT NULL COMMENT '负责人',
    hive_comment    TEXT         DEFAULT NULL COMMENT 'Hive 原始表注释',
    partition_cols  JSON         DEFAULT NULL COMMENT '分区字段列表',
    row_count_est   BIGINT       DEFAULT NULL COMMENT '估算行数（可选）',
    sample_questions TEXT        DEFAULT NULL COMMENT '示例问法，分号分隔',
    is_enabled      TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '是否参与问数',
    require_hint_for_expand TINYINT(1) NOT NULL DEFAULT 0 COMMENT '邻表扩展需问句点名，未点名不自动带入',
    source          VARCHAR(32)  NOT NULL DEFAULT 'hive' COMMENT '来源：hive/manual',
    synced_at       DATETIME     DEFAULT NULL COMMENT '上次从 Hive 同步时间',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_db_table (db_name, table_name)
) COMMENT='表级元数据';

CREATE TABLE IF NOT EXISTS column_meta (
    column_id       VARCHAR(64)  PRIMARY KEY COMMENT '内部ID，如 C001',
    table_id        VARCHAR(64)  NOT NULL COMMENT '关联 table_meta.table_id',
    column_name     VARCHAR(256) NOT NULL COMMENT '字段名',
    data_type       VARCHAR(128) NOT NULL COMMENT 'Hive 类型',
    description     TEXT         DEFAULT NULL COMMENT '业务含义',
    hive_comment    TEXT         DEFAULT NULL COMMENT 'Hive 原始字段注释',
    synonyms        JSON         DEFAULT NULL COMMENT '同义词数组，如 ["年纪","岁数"]',
    is_partition    TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否分区字段',
    is_sensitive    TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否敏感字段',
    is_enabled      TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '是否可用于问数',
    ordinal_pos     INT          DEFAULT NULL COMMENT '字段顺序',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_table_column (table_id, column_name),
    KEY idx_table_id (table_id),
    CONSTRAINT fk_column_table FOREIGN KEY (table_id) REFERENCES table_meta(table_id)
) COMMENT='字段级元数据';

CREATE TABLE IF NOT EXISTS table_relation (
    relation_id     VARCHAR(64)  PRIMARY KEY,
    left_db         VARCHAR(128) NOT NULL,
    left_table      VARCHAR(256) NOT NULL,
    left_column     VARCHAR(256) NOT NULL,
    right_db        VARCHAR(128) NOT NULL,
    right_table     VARCHAR(256) NOT NULL,
    right_column    VARCHAR(256) NOT NULL,
    join_type       VARCHAR(32)  NOT NULL DEFAULT 'LEFT JOIN' COMMENT 'LEFT/INNER 等',
    description     TEXT         DEFAULT NULL COMMENT '关联说明',
    is_enabled      TINYINT(1)   NOT NULL DEFAULT 1,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_left (left_db, left_table),
    KEY idx_right (right_db, right_table)
) COMMENT='表间 JOIN 关系';

CREATE TABLE IF NOT EXISTS metric_def (
    metric_id       VARCHAR(64)  PRIMARY KEY,
    metric_name     VARCHAR(128) NOT NULL COMMENT '指标英文名/标准名',
    cn_name         VARCHAR(128) DEFAULT NULL,
    aliases         JSON         DEFAULT NULL COMMENT '别名，如 ["日活","日活跃用户数"]',
    definition      TEXT         NOT NULL COMMENT '文字口径',
    sql_template    TEXT         DEFAULT NULL COMMENT 'Hive SQL 模板，占位符 ${date} 等',
    related_tables  JSON         DEFAULT NULL COMMENT '涉及表列表',
    domain          VARCHAR(64)  DEFAULT NULL,
    owner           VARCHAR(64)  DEFAULT NULL,
    is_enabled      TINYINT(1)   NOT NULL DEFAULT 1,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_metric_name (metric_name)
) COMMENT='指标定义（语义层）';

CREATE TABLE IF NOT EXISTS synonym (
    synonym_id      VARCHAR(64)  PRIMARY KEY,
    term            VARCHAR(256) NOT NULL COMMENT '用户可能说的词',
    target_type     VARCHAR(32)  NOT NULL COMMENT 'table/column/metric',
    target_id       VARCHAR(64)  NOT NULL COMMENT '指向 table_id/column_id/metric_id',
    is_enabled      TINYINT(1)   NOT NULL DEFAULT 1,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_term_target (term, target_type, target_id),
    KEY idx_term (term)
) COMMENT='同义词映射';
