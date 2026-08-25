-- 向量索引同步状态（向量库在外部 Qdrant，此处记录版本与 hash）

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
