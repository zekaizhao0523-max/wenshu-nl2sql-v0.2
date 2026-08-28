"""与 icecoding NL2SQL 对齐的 QueryPlan 结构（仅 SELECT，结构上不可表达写操作）。"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JoinSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    left_table: str
    right_table: str
    left_column: str
    right_column: str
    join_type: Literal["inner", "left", "right", "full", "cross"] = "inner"

    @field_validator("join_type", mode="before")
    @classmethod
    def normalize_join_type(cls, value):
        return str(value or "inner").strip().lower().replace(" outer", "")


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    column: str
    operator: Literal[
        "=",
        "!=",
        "<>",
        ">",
        ">=",
        "<",
        "<=",
        "in",
        "not in",
        "between",
        "like",
        "not like",
        "is",
        "is not",
    ]
    value: Any = None
    table: Optional[str] = None
    aggregation: Optional[Literal["count", "count_distinct", "sum", "avg", "min", "max"]] = None

    @field_validator("operator", mode="before")
    @classmethod
    def normalize_operator(cls, value):
        normalized = " ".join(str(value or "").strip().lower().split())
        return {
            "equals": "=",
            "equal": "=",
            "eq": "=",
            "not_equals": "!=",
            "gt": ">",
            "gte": ">=",
            "lt": "<",
            "lte": "<=",
        }.get(normalized, normalized)


class OutputFieldSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    concept: str = ""
    table: Optional[str] = None
    column: Optional[str] = None
    expression: Optional[str] = None
    alias: Optional[str] = None
    aggregation: Optional[Literal["count", "count_distinct", "sum", "avg", "min", "max"]] = None


class OrderSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    concept: str = ""
    table: Optional[str] = None
    column: Optional[str] = None
    expression: Optional[str] = None
    direction: Literal["asc", "desc"] = "asc"
    aggregation: Optional[Literal["count", "count_distinct", "sum", "avg", "min", "max"]] = None


class OutputGrain(BaseModel):
    """结果粒度（对齐 icecoding output_grain）。"""

    model_config = ConfigDict(extra="ignore")

    level: Literal["record", "entity", "aggregate", "global"] = "record"
    keys: list[str] = Field(default_factory=list)
    entity: Optional[str] = None


class QueryPlan(BaseModel):
    """查询计划：只有 SELECT 类元素，危险操作在结构上不可表达。"""

    model_config = ConfigDict(extra="ignore")

    target_tables: list[str] = Field(min_length=1)
    join_logic: list[JoinSpec] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    having: list[FilterSpec] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[OrderSpec] = Field(default_factory=list)
    limit: Optional[int] = Field(default=100, ge=1, le=5000)
    output_fields: list[OutputFieldSpec] = Field(default_factory=list)
    output_grain: OutputGrain = Field(default_factory=OutputGrain)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = "deterministic"

    def as_dict(self) -> dict:
        return self.model_dump()
