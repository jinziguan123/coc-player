"""OpenAPI 契约门禁的确定性回归。

此前契约有两个环境依赖/漂移点：
1. SPA 兜底路由是否进入 OpenAPI 取决于被 gitignore 的 ``apps/web/dist`` 是否存在，
   干净 checkout 里导出契约必然产生 diff；
2. 新端点合并后忘记重导 ``server/openapi.json``（曾漏掉 base-skills）。

本文件不读取磁盘上的 openapi.json，而是直接构建 app.openapi()，把这两条钉成
「无 dist 也稳定、新端点必须进契约」的不变量。
"""

from app.main import app


def _spec() -> dict:
    return app.openapi()


def test_spa_fallback_is_not_part_of_rest_contract():
    """SPA 路由不属于 REST 契约：无论本地是否已构建前端，schema 都不得包含 /{full_path}。"""
    spec = _spec()
    assert "/{full_path}" not in spec["paths"]
    assert not any(not path.startswith("/api/") for path in spec["paths"])


def test_base_skills_endpoint_is_declared_in_contract():
    """规则接口必须在契约中可见；漏导 openapi.json 时由 CI 的 diff 门禁兜底。"""
    spec = _spec()
    path = "/api/rules/{rule_system}/base-skills"
    assert path in spec["paths"]
    assert "post" in spec["paths"][path]
