-- 问数 Agent：L1 元数据 + L2 知识库 表结构（MySQL 8+）
-- 模块化模板见 sql/templates/；平台初始化推荐: python scripts/init_platform_schema.py --init
-- 向量库（Milvus/Qdrant）不存业务明细，仅存 embedding + payload，见 docs/

-- ============================================================
-- L1：结构化元数据（Schema 真相源）
-- ============================================================

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

-- ============================================================
-- L2：知识库（文档型 RAG）
-- ============================================================

CREATE TABLE IF NOT EXISTS kb_document (
    doc_id          VARCHAR(64)  PRIMARY KEY,
    title           VARCHAR(512) NOT NULL,
    doc_type        VARCHAR(32)  NOT NULL DEFAULT 'wiki' COMMENT 'wiki/pdf/md/faq',
    source_path     VARCHAR(1024) DEFAULT NULL COMMENT '原始文件路径或 URL',
    domain          VARCHAR(64)  DEFAULT NULL COMMENT '主题域',
    owner           VARCHAR(64)  DEFAULT NULL,
    version         VARCHAR(32)  DEFAULT NULL,
    is_enabled      TINYINT(1)   NOT NULL DEFAULT 1,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) COMMENT='知识库原始文档';

CREATE TABLE IF NOT EXISTS kb_chunk (
    chunk_id        VARCHAR(64)  PRIMARY KEY,
    doc_id          VARCHAR(64)  NOT NULL,
    chunk_index     INT          NOT NULL COMMENT '文档内序号',
    content         TEXT         NOT NULL COMMENT '段落正文',
    token_count     INT          DEFAULT NULL,
    is_enabled      TINYINT(1)   NOT NULL DEFAULT 1,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_doc_id (doc_id),
    CONSTRAINT fk_chunk_doc FOREIGN KEY (doc_id) REFERENCES kb_document(doc_id)
) COMMENT='知识库文档切片';

-- ============================================================
-- 向量索引同步状态（向量库在外部，此处记录版本）
-- ============================================================

CREATE TABLE IF NOT EXISTS vector_index_log (
    log_id          BIGINT       PRIMARY KEY AUTO_INCREMENT,
    object_type     VARCHAR(32)  NOT NULL COMMENT 'table/column/metric/join/doc_chunk',
    object_id       VARCHAR(64)  NOT NULL,
    embed_text_hash CHAR(64)     NOT NULL COMMENT 'embed 文本 SHA256，用于增量更新',
    vector_id       VARCHAR(128) NOT NULL COMMENT '向量库中的 point id',
    build_version   VARCHAR(32)  NOT NULL COMMENT '索引批次版本',
    indexed_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_object (object_type, object_id),
    KEY idx_build_version (build_version)
) COMMENT='向量索引同步日志';

-- ============================================================
-- 扫描暂存（用户审核后再写入 table_meta / column_meta）
-- ============================================================

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
