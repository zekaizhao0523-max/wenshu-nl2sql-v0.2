"""问数 Agent：QueryPlan → SQL → 风险审批 → 沙箱执行（对齐 icecoding NL2SQL）。"""

from wenshu.services.agent.plan_models import QueryPlan


def run_agent(*args, **kwargs):
    from wenshu.services.agent.orchestrator import run_agent as _run_agent

    return _run_agent(*args, **kwargs)


__all__ = ["run_agent", "QueryPlan"]
