"""Base abstractions for plug-and-play Hermes OS modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class HermesModule:
    """Represents one app tile on the Hermes OS Home Screen and its interactive screen."""

    id: str
    title: str
    tagline: str
    icon_svg: str
    nav_order: int = 10
    supports_lockscreen: bool = False
    lockscreen_slide: str | None = None
    summary_builder: Callable[[dict[str, Any]], tuple[str, str]] | None = None

    def to_tile(self, bundle: dict[str, Any]) -> dict[str, Any]:
        subtitle = self.tagline
        badge = ""
        if self.summary_builder is not None:
            try:
                subtitle, badge = self.summary_builder(bundle)
            except Exception:
                subtitle, badge = self.tagline, ""
        return {
            "id": self.id,
            "title": self.title,
            "tagline": self.tagline,
            "subtitle": subtitle,
            "badge": badge,
            "icon_svg": self.icon_svg,
            "supports_lockscreen": self.supports_lockscreen,
            "lockscreen_slide": self.lockscreen_slide,
        }
