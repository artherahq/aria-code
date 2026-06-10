"""
agents/realty — 经营权共创平台 AI Agent 模块
=============================================
9 个领域专属 Agent，继承 BaseAgent，用于：
  - 资产诊断与处置建议
  - 业态匹配与经营方推荐
  - 合同条款结构化生成
  - 分账规则配置建议
  - 流水核验与异常检测
  - 能耗异常分析
  - 合同履约风控
  - 运营优化建议
  - 退出清算方案生成

用法:
    from agents.realty import AssetDiagnosisAgent, RevenueShareAgent
    from agents.realty import REALTY_TEAM  # 默认 team 名称列表
"""

from .asset_diagnosis  import AssetDiagnosisAgent
from .business_match   import BusinessMatchAgent
from .contract_rules   import ContractRulesAgent
from .revenue_share    import RevenueShareAgent
from .cashflow_verify  import CashFlowVerifyAgent
from .energy_anomaly   import EnergyAnomalyAgent
from .fulfillment_risk import FulfillmentRiskAgent
from .ops_optimize     import OpsOptimizeAgent
from .exit_settlement  import ExitSettlementAgent

# 默认完整 team
REALTY_TEAM = [
    "asset_diagnosis",
    "business_match",
    "contract_rules",
    "revenue_share",
    "cashflow_verify",
    "energy_anomaly",
    "fulfillment_risk",
    "ops_optimize",
    "exit_settlement",
]

# 风控专项 team（快速预警）
RISK_TEAM = [
    "cashflow_verify",
    "energy_anomaly",
    "fulfillment_risk",
]

__all__ = [
    "AssetDiagnosisAgent",
    "BusinessMatchAgent",
    "ContractRulesAgent",
    "RevenueShareAgent",
    "CashFlowVerifyAgent",
    "EnergyAnomalyAgent",
    "FulfillmentRiskAgent",
    "OpsOptimizeAgent",
    "ExitSettlementAgent",
    "REALTY_TEAM",
    "RISK_TEAM",
]
