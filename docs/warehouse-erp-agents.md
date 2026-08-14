# 仓储 ERP Agents

仓储 Agents 是只读的运营风险分析器；它们不保存 ERP、WMS 或货代凭据，也不会修改库存、入库单或运输单。

## 可用 Agent

- `warehouse_logistics_sync`：读取 `connectors`，检查 `name`、`delay_minutes`、`failed_jobs`。
- `warehouse_inbound_exceptions`：读取 `inbounds`，检查 `id`、`expected_qty`、`received_qty`、`damaged_qty`、`overdue`。
- `warehouse_inventory_health`：读取 `skus` 与 `locations`，检查安全库存和库位利用率。

调用 `AgentTeam` 时请使用 `WAREHOUSE_TEAM` 与 `WAREHOUSE_SCHEME`，不要使用金融默认信号方案：

```python
from agents.team import AgentTeam
from agents.warehouse import WAREHOUSE_SCHEME, WAREHOUSE_TEAM

team = AgentTeam(signal_scheme=WAREHOUSE_SCHEME)
result = await team.run("WH-CN-01", agents=WAREHOUSE_TEAM, agent_data={...})
```

仓库包含一个最小只读 ERP 快照客户端。生产 ERP/WMS 应在上游完成认证、分页、重试、审计和字段校验，然后仅把上述最小只读数据契约传入 Agent。

## CLI 与 ERP 快照端点

配置 `ARIA_WAREHOUSE_ERP_URL` 与只读 `ARIA_WAREHOUSE_ERP_TOKEN` 后，可运行：

```text
/warehouse WH-CN-01
/warehouse WH-CN-01 --json
```

客户端只发出一个 `GET` 请求，默认端点为
`/api/v1/warehouses/{warehouse_id}/agent-snapshot`。响应可直接是对象，或包在
`data` 对象中；其中只会读取 `connectors`、`inbounds`、`skus`、`locations` 四个列表。
不会调用任何写入型 ERP API。
