"""Packet declarations. Importing this package populates REGISTRY (in documentation order)."""

from importlib import import_module

for _name in ("vehicle", "link", "host", "legacy"):
    import_module(f"{__name__}.{_name}")
