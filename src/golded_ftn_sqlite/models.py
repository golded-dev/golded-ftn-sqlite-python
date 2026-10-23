from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from golded_ftn import FtnAddress


class AreaKind(StrEnum):
    NETMAIL = "netmail"
    ECHOMAIL = "echomail"


@dataclass(frozen=True, slots=True, kw_only=True)
class AreaDefinition:
    code: str
    name: str
    kind: AreaKind
    default_origin: FtnAddress | None = None
    sort_order: int = 0
    meta_key: str | None = None

    def __post_init__(self) -> None:
        if not self.code or self.code != self.code.strip():
            raise ValueError(
                "area code must be non-empty and have no surrounding space"
            )
        if not self.name or self.name != self.name.strip():
            raise ValueError(
                "area name must be non-empty and have no surrounding space"
            )
