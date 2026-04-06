"""
Logging configuration for Polymarket Arbitrage Bot.
Call setup_logging() once at startup. All other modules use get_logger(__name__).
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Custom TRADE level between INFO (20) and WARNING (30)
TRADE_LEVEL = 25
logging.addLevelName(TRADE_LEVEL, "TRADE")


def trade(self, message, *args, **kwargs):
    if self.isEnabledFor(TRADE_LEVEL):
        self._log(TRADE_LEVEL, message, args, **kwargs)


logging.Logger.trade = trade

# ANSI color codes
COLORS = {
    logging.DEBUG: "\033[36m",     # Cyan
    logging.INFO: "\033[32m",      # Green
    TRADE_LEVEL: "\033[35m",       # Magenta
    logging.WARNING: "\033[33m",   # Yellow
    logging.ERROR: "\033[31m",     # Red
    logging.CRITICAL: "\033[1;31m",# Bold Red
}
RESET = "\033[0m"


class ColorFormatter(logging.Formatter):
    def format(self, record):
        color = COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname:<8}{RESET}"
        return super().format(record)


def setup_logging(log_level: str = "INFO") -> None:
    """Initialize root logger with file + terminal handlers."""
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # capture everything; handlers filter

    fmt = "%(asctime)s [%(levelname)-8s] %(name)-22s — %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    # Terminal handler (colored)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(numeric_level)
    stream_handler.setFormatter(ColorFormatter(fmt, datefmt=date_fmt))
    root.addHandler(stream_handler)

    # Main bot.log (all levels)
    file_handler = RotatingFileHandler(
        logs_dir / "bot.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))
    root.addHandler(file_handler)

    # Trades-only log
    trades_handler = RotatingFileHandler(
        logs_dir / "trades.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=10,
        encoding="utf-8",
    )
    trades_handler.setLevel(TRADE_LEVEL)
    trades_handler.addFilter(_TradeLevelFilter())
    trades_handler.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))
    root.addHandler(trades_handler)


class _TradeLevelFilter(logging.Filter):
    """Only pass TRADE-level records."""
    def filter(self, record):
        return record.levelno == TRADE_LEVEL


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Call after setup_logging() has been called."""
    return logging.getLogger(name)
