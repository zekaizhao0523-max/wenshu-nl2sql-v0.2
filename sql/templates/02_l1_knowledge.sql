-- L2：知识库（文档型 RAG）

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
