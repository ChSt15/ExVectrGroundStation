"""TEMPORARY legacy telecommand sender (user decision 2026-11-20): a third uplink, to be deleted with the flight
software's T9b."""

from .controller import TelecommandController, TelecommandError, TelecommandSent

__all__ = ["TelecommandController", "TelecommandError", "TelecommandSent"]
