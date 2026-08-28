"""LogicalPlan：QueryPlan → 可审计关系算子 DAG（对齐 icecoding logical_planner 精简版）。"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from wenshu.services.agent.plan_models import (
    FilterSpec,
    JoinSpec,
    OutputFieldSpec,
    OutputGrain,
    QueryPlan,
)


class LogicalOperation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    kind: Literal[
        "scan",
        "join",
        "semi_join",
        "anti_join",
        "filter",
        "aggregate",
        "having",
        "project",
        "sort",
        "limit",
    ]
    inputs: list[str] = Field(default_factory=list)
    table: Optional[str] = None
    join: Optional[JoinSpec] = None
    predicates: list[FilterSpec] = Field(default_factory=list)
    fields: list[OutputFieldSpec] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    sort_by: list[str] = Field(default_factory=list)
    limit: Optional[int] = None


class LogicalPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    operations: list[LogicalOperation] = Field(default_factory=list)
    root_operation_id: str = ""
    output_fields: list[OutputFieldSpec] = Field(default_factory=list)
    output_grain: OutputGrain = Field(default_factory=OutputGrain)
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return self.model_dump()


def infer_output_grain(plan: QueryPlan, *, question: str = "", semantic_graph: dict | None = None) -> OutputGrain:
    graph = semantic_graph or {}
    has_agg = any(f.aggregation for f in plan.output_fields)
    group_by = list(plan.group_by or [])
    action = str(graph.get("query_action") or "")
    qtype = str(graph.get("query_type") or "")
    if has_agg and not group_by:
        return OutputGrain(level="global", keys=[])
    if has_agg or group_by or action == "aggregate" or qtype in {"aggregation", "multi_fact"}:
        return OutputGrain(level="aggregate", keys=list(group_by))
    if action in {"lookup", "detail"} or qtype in {"attribute_lookup", "event_detail", "fact_filter"}:
        return OutputGrain(level="record", keys=list(group_by))
    if "平均" in (question or "") or "合计" in (question or "") or "统计" in (question or ""):
        return OutputGrain(level="global" if has_agg else "record", keys=list(group_by))
    return OutputGrain(level="record", keys=[])


def build_logical_plan(
    plan: QueryPlan,
    *,
    question: str = "",
    semantic_graph: dict | None = None,
) -> LogicalPlan:
    """把扁平 QueryPlan 转成 scan→join→filter→aggregate→project 算子链。"""
    if any(f.aggregation for f in plan.output_fields) or plan.group_by:
        grain = infer_output_grain(plan, question=question, semantic_graph=semantic_graph)
    else:
        grain = plan.output_grain or infer_output_grain(
            plan, question=question, semantic_graph=semantic_graph
        )

    operations: list[LogicalOperation] = []
    scan_ids: dict[str, str] = {}
    for index, table in enumerate(plan.target_tables, 1):
        scan_id = f"scan_{index}"
        scan_ids[table] = scan_id
        operations.append(LogicalOperation(id=scan_id, kind="scan", table=table))

    if not plan.target_tables:
        return LogicalPlan(operations=[], root_operation_id="", output_fields=plan.output_fields, output_grain=grain)

    root = scan_ids[plan.target_tables[0]]
    for index, join in enumerate(plan.join_logic, 1):
        right_input = scan_ids.get(join.right_table, scan_ids.get(join.left_table, root))
        op_id = f"join_{index}"
        operations.append(
            LogicalOperation(
                id=op_id,
                kind="join",
                inputs=list(dict.fromkeys([root, right_input])),
                join=join,
            )
        )
        root = op_id

    if plan.filters:
        operations.append(
            LogicalOperation(id="filter_1", kind="filter", inputs=[root], predicates=list(plan.filters))
        )
        root = "filter_1"

    if plan.group_by or any(f.aggregation for f in plan.output_fields):
        operations.append(
            LogicalOperation(
                id="aggregate_1",
                kind="aggregate",
                inputs=[root],
                group_by=list(plan.group_by),
            )
        )
        root = "aggregate_1"

    if plan.having:
        operations.append(
            LogicalOperation(id="having_1", kind="having", inputs=[root], predicates=list(plan.having))
        )
        root = "having_1"

    operations.append(
        LogicalOperation(id="project_1", kind="project", inputs=[root], fields=list(plan.output_fields))
    )
    root = "project_1"

    if plan.order_by:
        operations.append(
            LogicalOperation(
                id="sort_1",
                kind="sort",
                inputs=[root],
                sort_by=[f"{o.concept or o.column or ''} {o.direction.upper()}" for o in plan.order_by],
            )
        )
        root = "sort_1"

    if plan.limit:
        operations.append(LogicalOperation(id="limit_1", kind="limit", inputs=[root], limit=plan.limit))
        root = "limit_1"

    return LogicalPlan(
        operations=operations,
        root_operation_id=root,
        output_fields=list(plan.output_fields),
        output_grain=grain,
        confidence=float(plan.confidence or 0.0),
    )


def validate_logical_plan(
    plan: LogicalPlan,
    *,
    relation_cardinalities: list[dict[str, Any]] | None = None,
) -> list[str]:
    """结构完整性 + 粒度/JOIN 放大风险（对齐 icecoding validate_logical_plan）。"""
    errors: list[str] = []
    op_ids = {op.id for op in plan.operations}
    if len(op_ids) != len(plan.operations):
        errors.append("LogicalPlan operation id 重复")
    if plan.root_operation_id and plan.root_operation_id not in op_ids:
        errors.append("LogicalPlan root_operation_id 不存在")
    for op in plan.operations:
        missing = set(op.inputs) - op_ids
        if missing:
            errors.append(f"LogicalPlan 操作 {op.id} 引用了不存在的输入 {sorted(missing)}")

    if plan.output_grain.level == "aggregate":
        agg = next((op for op in plan.operations if op.kind == "aggregate"), None)
        if agg is None:
            errors.append("输出粒度为 aggregate，但 LogicalPlan 缺少 aggregate 操作")
        elif plan.output_grain.keys and set(plan.output_grain.keys) != set(agg.group_by):
            errors.append("输出粒度 keys 与 aggregate.group_by 不一致")
    if plan.output_grain.level == "global" and plan.output_grain.keys:
        errors.append("global 输出粒度不能包含分组 keys")
    if plan.output_grain.level == "global":
        agg = next((op for op in plan.operations if op.kind == "aggregate"), None)
        if agg is None:
            errors.append("输出粒度为 global，但 LogicalPlan 缺少 aggregate 操作")

    relations = relation_cardinalities or []
    for op in plan.operations:
        if op.kind != "join" or op.join is None:
            continue
        join = op.join
        rel = next(
            (
                r
                for r in relations
                if {str(r.get("left_table") or r.get("source_table") or "").lower(),
                    str(r.get("right_table") or r.get("target_table") or "").lower()}
                == {join.left_table.lower(), join.right_table.lower()}
            ),
            None,
        )
        if not rel:
            continue
        cardinality = str(rel.get("cardinality") or "").lower()
        many_to_many = cardinality in {"many_to_many", "n:m", "many-to-many"}
        if plan.output_grain.level in {"aggregate", "global"} and many_to_many:
            errors.append(
                f"聚合查询使用 {cardinality or 'many_to_many'} JOIN 可能重复累计指标；"
                "应先按共同粒度预聚合或修正关系基数"
            )
    return errors
