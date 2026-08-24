from dataclasses import dataclass, field
from typing import Any, Dict, Callable

@dataclass
class AriaContext:
    """
    Unified context for Aria CLI commands.
    This replaces the dynamic `__globals__` injection pattern.
    Commands should accept this context to access console, workspace, and config.
    """
    console: Any = None
    workspace: Any = None
    config: Dict[str, Any] = field(default_factory=dict)
    has_rich: bool = False
    
    # Callbacks for config
    save_config_cb: Callable[[Dict[str, Any]], None] = None
    load_config_cb: Callable[[], Dict[str, Any]] = None
    
    def save_config(self, cfg: dict):
        if self.save_config_cb:
            self.save_config_cb(cfg)
            
    def load_config(self) -> dict:
        if self.load_config_cb:
            return self.load_config_cb()
        return {}
    
    def __post_init__(self):
        if self.config is None:
            self.config = {}
        if self.has_rich is False and self.console is not None:
            self.has_rich = True

