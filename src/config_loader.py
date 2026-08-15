"""تحميل الإعدادات من YAML + متغيرات البيئة.

قاعدة الأسرار: لا يُكتب أي توكن أو كلمة مرور في ملفات YAML.
تُقرأ حصراً من البيئة (أو ملف .env محلي غير مُتتبَّع في git).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# جذر المشروع = المجلد الأب لـ src/
ROOT = Path(__file__).resolve().parent.parent

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(RuntimeError):
    """إعداد ناقص أو غير صالح — يوقف التشغيل مبكراً بدل الفشل أثناء التداول."""


def _coerce(value: str) -> Any:
    low = value.strip().lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _load_dotenv(path: Path) -> None:
    """قراءة ملف .env بسيط دون اعتماد مكتبة خارجية."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip("'\""))


def _apply_env_overrides(data: dict[str, Any], prefix: str = "GD_") -> dict[str, Any]:
    """تجاوز أي مفتاح عبر البيئة بالصيغة GD_<SECTION>__<KEY>[__<KEY>]."""
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix) or "__" not in env_key:
            continue
        path = [p.lower() for p in env_key[len(prefix):].split("__")]
        node: Any = data
        for part in path[:-1]:
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if isinstance(node, dict) and path[-1] in node:
            node[path[-1]] = _coerce(env_val)
    return data


def _deep_get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


@dataclass
class Config:
    """حاوية الإعدادات — وصول بالمسار النقطي: ``cfg.get("bot.symbol")``."""

    data: dict[str, Any] = field(default_factory=dict)
    root: Path = ROOT

    def get(self, path: str, default: Any = None) -> Any:
        return _deep_get(self.data, path, default)

    def require(self, path: str) -> Any:
        value = self.get(path)
        if value is None:
            raise ConfigError(f"إعداد مطلوب غير موجود: {path}")
        return value

    def path(self, path: str, default: str = "") -> Path:
        """مسار ملف نسبي لجذر المشروع."""
        raw = self.get(path, default) or default
        candidate = Path(raw)
        return candidate if candidate.is_absolute() else self.root / candidate

    def secret(self, env_key: str, required: bool = False) -> str | None:
        value = os.environ.get(env_key)
        if required and not value:
            raise ConfigError(
                f"سر مطلوب غير موجود في البيئة: {env_key} (راجع .env.example)"
            )
        return value

    # اختصارات كثيرة الاستخدام ------------------------------------------------
    @property
    def symbol(self) -> str:
        return str(self.get("bot.symbol", "XAUUSD"))

    @property
    def dry_run(self) -> bool:
        return bool(self.get("bot.dry_run", True))


def load_config(config_dir: Path | str | None = None) -> Config:
    """تحميل settings + risk + symbols في كائن واحد مع تجاوزات البيئة."""
    root = ROOT
    cdir = Path(config_dir) if config_dir else root / "config"
    _load_dotenv(root / ".env")

    merged: dict[str, Any] = {}
    for name in ("settings.yaml", "risk.yaml", "symbols.yaml"):
        file = cdir / name
        if not file.exists():
            raise ConfigError(f"ملف إعدادات مفقود: {file}")
        loaded = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"ملف الإعدادات ليس قاموساً صالحاً: {file}")
        overlap = merged.keys() & loaded.keys()
        if overlap:
            raise ConfigError(f"تعارض في أقسام الإعدادات {sorted(overlap)} داخل {name}")
        merged.update(loaded)

    _apply_env_overrides(merged)
    cfg = Config(data=merged, root=root)
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    """فحوصات صلاحية أساسية — تفشل قبل أي اتصال بالوسيط."""
    symbol = cfg.symbol
    allowed = cfg.get("allowed", [])
    if symbol not in allowed:
        raise ConfigError(f"الرمز {symbol} خارج القائمة المسموحة {allowed}")

    risk_per_trade = float(cfg.get("risk.risk_per_trade", 0))
    max_risk = float(cfg.get("risk.max_risk_per_trade", 0.02))
    if not 0 < risk_per_trade <= max_risk:
        raise ConfigError(
            f"risk_per_trade={risk_per_trade} يجب أن يكون بين 0 و max_risk_per_trade={max_risk}"
        )
    if max_risk > 0.02:
        raise ConfigError("max_risk_per_trade لا يجوز أن يتجاوز 2% حسب البند 8.1")

    if float(cfg.get("risk.max_total_risk", 0)) < risk_per_trade:
        raise ConfigError("max_total_risk أصغر من مخاطرة الصفقة الواحدة")

    min_acs = float(cfg.get("gold_dragon.min_acs", 0))
    if not 0 < min_acs <= 10:
        raise ConfigError("gold_dragon.min_acs يجب أن يكون بين 0 و 10")

    feed = str(cfg.get("bot.feed", "synthetic"))
    if feed not in {"mt5", "csv", "synthetic"}:
        raise ConfigError(f"bot.feed غير مدعوم: {feed}")

    if cfg.get("challenge_mode.enabled") and not cfg.get("challenge_mode.accepted_contract"):
        raise ConfigError(
            "وضع التحدي مُفعّل دون إقرار عقد التحدي — اضبط challenge_mode.accepted_contract: true"
        )
