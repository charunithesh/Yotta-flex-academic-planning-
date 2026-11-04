from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/yotta_flex.db")
    app_name: str = "Yotta Flex | Academic Planning"
    max_section_capacity: int = int(os.getenv("MAX_SECTION_CAPACITY", "60"))


settings = Settings()
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
