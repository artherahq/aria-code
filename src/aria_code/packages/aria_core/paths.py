"""用户状态目录的唯一解析入口。

2026-08-19 起因：本仓库有三个并存的用户状态目录，同一个用户的数据被拆散在
不同地方：

    ~/.arthera/     169MB —— providers.json(API keys) / brokers.json(券商凭证)
                              / portfolio.db / sessions / memory / models …
    ~/.aria/        —— daemon.db / datasources.yaml / providers.yaml / task_ledger
    ~/.aria-code/   —— resolve_config_dir() 给新用户的默认目录

``apps/cli/config_paths.resolve_config_dir()`` 已经做了正确的事：认
``ARIA_HOME``，老用户(存在 ~/.arthera)沿用旧目录，新用户落到 ~/.aria-code。
问题是**只有配置文件走了那一层**——另外 38 个文件直接写死
``Path.home() / ".arthera"``，绕过了它。后果是一个全新用户的 config.json 会
落在 ~/.aria-code/，而他的 API key 和券商凭证会被写进 ~/.arthera/：两个目录，
同一个用户。这不是命名审美问题，是状态管理缺陷。

本模块把那套优先级抽成所有层都能 import 的单一实现：

    ARIA_HOME 环境变量  >  已存在的 ~/.arthera(老用户，不迁移)  >  ~/.aria-code

放在 packages/aria_core 而不是 apps/cli，是因为调用方横跨 runtime/、brokers/、
packages/、datasources/、tools/ —— 让这些层反向 import aria_code.apps/cli 是分层倒置。
本模块只依赖 stdlib，任何层都能安全引入。

⚠️ 对既有用户零影响：只要 ~/.arthera 存在，aria_home() 就返回它，行为与硬编码
时完全一致，不会移动任何文件。变化只对全新安装生效——所有状态统一落在一个
目录里。真正的数据迁移(把 ~/.arthera 搬到 ~/.aria-code)是另一件事，需要显式
的用户确认，不在本模块职责内。
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["aria_home", "LEGACY_HOME_NAME", "DEFAULT_HOME_NAME"]

LEGACY_HOME_NAME = ".arthera"
DEFAULT_HOME_NAME = ".aria-code"


def aria_home() -> Path:
    """返回用户状态根目录。不创建目录——由调用方按需 mkdir。

    优先级与 apps/cli/config_paths.resolve_config_dir() 保持一致；那个函数现在
    委托到这里，避免两处各自演化。
    """
    override = os.environ.get("ARIA_HOME")
    if override:
        return Path(override).expanduser()

    legacy = Path.home() / LEGACY_HOME_NAME
    if legacy.exists():
        return legacy

    return Path.home() / DEFAULT_HOME_NAME
