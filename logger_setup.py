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

# ANSI escape codes
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_MAGENTA = "\033[35m"
_BOLD_RED = "\033[1;31m"
_BOLD_GREEN = "\033[1;32m"
_BOLD_CYAN = "\033[1;36m"
_BOLD_YELLOW = "\033[1;33m"
_BOLD_MAGENTA = "\033[1;35m"
_BG_RED = "\033[41m"
_WHITE = "\033[37m"

# Level → (color, icon, label)
_LEVEL_STYLE = {
    logging.DEBUG:    (_DIM + _CYAN, " ", "DBG"),
    logging.INFO:     (_GREEN,       " ", "INF"),
    TRADE_LEVEL:      (_BOLD_MAGENTA," ", "TRD"),
    logging.WARNING:  (_BOLD_YELLOW, " ", "WRN"),
    logging.ERROR:    (_RED,         " ", "ERR"),
    logging.CRITICAL: (_BG_RED + _WHITE, " ", "CRT"),
}

# Module name short aliases for cleaner output
_MODULE_SHORT = {
    "__main__": "main",
    "main": "main",
    "scanner": "scan",
    "arbitrage": "arb",
    "ai_analyzer": "ai",
    "executor": "exec",
    "portfolio": "port",
    "risk_manager": "risk",
    "compounder": "comp",
    "data_feeds": "odds",
    "match_analytics": "match",
    "realtime_feed": "ws",
    "shadow_tracker": "shadow",
    "risk_events": "journal",
    "btc.main_btc": "btc",
    "btc.rtds_feed": "rtds",
    "btc.btc_scanner": "bscan",
    "btc.signal_engine": "signal",
}


class ConsoleFormatter(logging.Formatter):
    """Clean, compact console formatter with color and icons."""

    def format(self, record):
        color, icon, label = _LEVEL_STYLE.get(
            record.levelno, (_GREEN, " ", "???")
        )

        # Shorten module name
        short_name = _MODULE_SHORT.get(record.name, record.name)
        if "." in short_name:
            short_name = short_name.rsplit(".", 1)[-1]
            short_name = _MODULE_SHORT.get(short_name, short_name)

        ts = self.formatTime(record, "%H:%M:%S")
        msg = record.getMessage()

        # Cycle headers get special treatment — they are the visual anchor
        if msg.startswith("== CYCLE"):
            return (
                f"{_DIM}{ts}{_RESET}  "
                f"{_BOLD_CYAN}{msg}{_RESET}"
            )

        # Box-style status blocks (portfolio, etc.) pass through with color
        if msg.startswith("+--") or msg.startswith("|  ") or msg.startswith("+-"):
            return f"{_DIM}{ts}{_RESET}  {color}{msg}{_RESET}"

        # Trade events get a highlighted bar
        if record.levelno == TRADE_LEVEL:
            return (
                f"{_DIM}{ts}{_RESET}  "
                f"{_BOLD_MAGENTA}{icon} {label}{_RESET}  "
                f"{_BOLD}{msg}{_RESET}"
            )

        # Warning/Error/Critical stand out
        if record.levelno >= logging.WARNING:
            return (
                f"{_DIM}{ts}{_RESET}  "
                f"{color}{icon} {label}{_RESET}  "
                f"{_DIM}[{short_name}]{_RESET} {color}{msg}{_RESET}"
            )

        # Indented detail lines (start with spaces) — dimmer, no module tag
        if msg.startswith("  "):
            return (
                f"{_DIM}{ts}{_RESET}  "
                f"     {_DIM}{msg}{_RESET}"
            )

        # Normal INFO
        return (
            f"{_DIM}{ts}{_RESET}  "
            f"{color}{icon} {label}{_RESET}  "
            f"{_DIM}[{short_name}]{_RESET} {msg}"
        )


class FileFormatter(logging.Formatter):
    """Plain text formatter for log files — no ANSI, full module name."""

    def format(self, record):
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        level = record.levelname
        name = record.name
        msg = record.getMessage()
        return f"{ts} [{level:<8}] {name:<20} | {msg}"


def setup_logging(log_level: str = "INFO") -> None:
    """Initialize root logger with file + terminal handlers."""
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # capture everything; handlers filter

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "anthropic._base_client", "urllib3", "web3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Terminal handler (clean format, force UTF-8 on Windows)
    import io
    if hasattr(sys.stdout, "buffer"):
        stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    else:
        stream = sys.stdout
    stream_handler = logging.StreamHandler(stream)
    stream_handler.setLevel(numeric_level)
    stream_handler.setFormatter(ConsoleFormatter())
    root.addHandler(stream_handler)

    # Main bot.log (all levels, plain text)
    file_handler = RotatingFileHandler(
        logs_dir / "bot.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(FileFormatter())
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
    trades_handler.setFormatter(FileFormatter())
    root.addHandler(trades_handler)


class _TradeLevelFilter(logging.Filter):
    """Only pass TRADE-level records."""
    def filter(self, record):
        return record.levelno == TRADE_LEVEL


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Call after setup_logging() has been called."""
    return logging.getLogger(name)
