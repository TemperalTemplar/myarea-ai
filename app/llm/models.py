"""
Model registry.

Each entry describes a logical role → physical Ollama model string.
Swap model strings here without touching call sites.
"""
from dataclasses import dataclass
from flask import current_app


@dataclass
class ModelDef:
    role: str
    description: str

    @property
    def name(self) -> str:
        cfg_key = f"{self.role.upper()}_MODEL"
        return current_app.config.get(cfg_key, "gemma2:2b")


DISPATCHER = ModelDef(role="dispatcher", description="Lean intent classifier")
SILEX      = ModelDef(role="silex",      description="Full Silex personality model")
