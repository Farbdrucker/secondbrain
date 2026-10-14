import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    brain: Path
    tz: ZoneInfo
    max_attempts: int
    claude_model: str
    claude_timeout: int

    @property
    def inbox(self) -> Path:
        return self.brain / "inbox"

    @property
    def failed(self) -> Path:
        return self.brain / "inbox" / "failed"

    @property
    def raw(self) -> Path:
        return self.brain / "raw"

    @property
    def wiki(self) -> Path:
        return self.brain / "wiki"

    def ensure_dirs(self) -> None:
        for d in (self.inbox, self.failed, self.raw, self.wiki):
            d.mkdir(parents=True, exist_ok=True)


def load() -> Config:
    return Config(
        brain=Path(os.environ.get("BRAIN_DIR", "./brain")),
        tz=ZoneInfo(os.environ.get("BRAIN_TZ") or "Europe/Berlin"),
        max_attempts=int(os.environ.get("MAX_ATTEMPTS", "3")),
        claude_model=os.environ.get("CLAUDE_MODEL", "sonnet"),
        claude_timeout=int(os.environ.get("CLAUDE_TIMEOUT", "300")),
    )
