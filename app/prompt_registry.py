from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.prompts import (
    ANALYSIS_SCHEMA,
    ANALYSIS_SYSTEM_PROMPT,
    DEFAULT_IMAGE_EDIT_PROMPT,
    INTEGRITY_SCHEMA,
    INTEGRITY_SYSTEM_PROMPT,
)


@dataclass(frozen=True)
class PromptSnapshot:
    name: str
    version: str
    content: str
    schema: dict[str, Any]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


class PromptRegistry:
    """Registro inmutable en runtime; cada generación guarda su snapshot y hash."""

    _prompts = {
        "analysis_system": ("1.0.0", ANALYSIS_SYSTEM_PROMPT, ANALYSIS_SCHEMA),
        "integrity_system": ("1.0.0", INTEGRITY_SYSTEM_PROMPT, INTEGRITY_SCHEMA),
        "image_edit_default": ("1.0.0", DEFAULT_IMAGE_EDIT_PROMPT, {}),
    }

    @classmethod
    def get(cls, name: str) -> PromptSnapshot:
        try:
            version, content, schema = cls._prompts[name]
        except KeyError as error:
            raise KeyError(f"Prompt no registrado: {name}") from error
        return PromptSnapshot(name=name, version=version, content=content, schema=schema)
