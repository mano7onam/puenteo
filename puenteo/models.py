from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Message:
    role: str  # user | assistant | system | tool | reasoning
    text: str = ""
    timestamp: str = ""
    index: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    def preview(self, n: int = 160) -> str:
        t = " ".join((self.text or "").split())
        return t if len(t) <= n else t[: n - 1] + "…"


@dataclass
class Session:
    provider: str  # claude_code | codex | grok
    session_id: str
    path: str
    title: str = ""
    cwd: str = ""
    mtime: float = 0.0
    size: int = 0
    message_count: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Transcript:
    session: Session
    messages: List[Message] = field(default_factory=list)

    def text_messages(self, *, roles: Optional[List[str]] = None) -> List[Message]:
        if roles is None:
            roles = ["user", "assistant"]
        return [m for m in self.messages if m.role in roles and (m.text or "").strip()]
