"""Persistent user configuration (``~/.config/raddu/config.json``)."""

from __future__ import annotations

import json
import os
import pathlib

from .catalog import CatalogError, catalog

APP = "raddu"


def config_dir() -> pathlib.Path:
    base = os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config"
    return pathlib.Path(base) / APP


def config_path() -> pathlib.Path:
    return config_dir() / "config.json"


def data_dir() -> pathlib.Path:
    base = os.environ.get("XDG_DATA_HOME") or pathlib.Path.home() / ".local" / "share"
    return pathlib.Path(base) / APP


def offline_dir() -> pathlib.Path:
    custom = load().get("storage") or load().get("offline_dir")
    if custom:
        return pathlib.Path(custom).expanduser()
    return data_dir() / "voices"


def load() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict) -> pathlib.Path:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def default_reciter() -> str:
    return str(load().get("reciter") or "aft")


def set_value(key: str, value: str) -> str:
    cat = catalog()
    if key == "reciter":
        if value.lower() in ("clear", "none", "reset"):
            data = load()
            data.pop("reciter", None)
            save(data)
            return "reciter cleared - falling back to built-in default (aft)"
        if cat.find_reciter(value) is None:
            raise CatalogError(f"unknown reciter {value!r}; see 'raddu -R'")
    if key == "style":
        # validated against every reciter so an obvious typo is caught now
        for r in cat.reciters:
            if r.style_by_code(value) is not None:
                break
        else:
            raise CatalogError(f"unknown style {value!r}; see 'raddu -R'")
    if key == "player":
        from .player import KNOWN_PLAYERS
        if value.lower() in ("clear", "none", "auto"):
            value = ""
        elif value not in {p.name for p in KNOWN_PLAYERS}:
            raise CatalogError(
                f"unknown player {value!r}; known: "
                + ", ".join(p.name for p in KNOWN_PLAYERS)
            )
    if key == "salawat":
        from .salawat import SalawatError, parse_minutes
        try:
            minutes = parse_minutes(value)
        except SalawatError as exc:
            raise CatalogError(str(exc)) from None
        data = load()
        if minutes is None:
            data.pop("salawat", None)
            save(data)
            return "salawat interval cleared - 'raddu --salawat' uses 10 minutes"
        data["salawat"] = f"{minutes:g}"
        save(data)
        return f"salawat = every {minutes:g} minute(s) when you run 'raddu --salawat'"
    if key in ("salawat_file", "adhan_file"):
        builtin = {"salawat_file": "al-Ahzab 33:56, al-Afasy",
                   "adhan_file": "adhan of Sabah Fakhri"}[key]
        data = load()
        if value.lower() in ("clear", "none", "reset", "default", ""):
            data.pop(key, None)
            save(data)
            return f"{key} cleared - using the built-in clip ({builtin})"
        p = pathlib.Path(value).expanduser().resolve()
        if not p.is_file():
            raise CatalogError(f"{key}: no such file {str(p)!r}")
        data[key] = str(p)
        save(data)
        return f"{key} = {p}"
    if key in ("location", "method", "asr"):
        from . import prayer
        data = load()
        if value.lower() in ("clear", "none", "reset", "default", ""):
            data.pop(key, None)
            save(data)
            return f"{key} reset to default ({prayer.location().name if key == 'location' else 'auto' if key == 'method' else 'standard'})"
        try:
            if key == "location":
                label, *_ = prayer.parse_place(value)
                stored = value.strip()
            elif key == "method":
                stored = label = prayer.parse_method(value)
            else:
                stored = label = prayer.parse_asr(value)
        except prayer.PrayerError as exc:
            raise CatalogError(str(exc)) from None
        data[key] = stored
        save(data)
        return f"{key} = {label}"
    if key in ("storage", "offline_dir"):
        if value.lower() in ("clear", "none", "reset", "default"):
            data = load()
            data.pop("storage", None)
            data.pop("offline_dir", None)
            save(data)
            return f"storage reset to default ({offline_dir()})"
        p = pathlib.Path(value).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        data = load()
        data["storage"] = str(p)
        save(data)
        return f"storage = {p}"
    data = load()
    if value == "":
        data.pop(key, None)
    else:
        data[key] = value
    save(data)
    return f"{key} = {value or '(auto)'}"