"""Logging estructurado y métricas para poke_player.

Provee configuración centralizada de logging con soporte para:
- Logs en formato JSON para producción
- Logs en formato legible para desarrollo
- Métricas de rendimiento (tiempos de respuesta, contadores)
- Contexto correlacional (request_id, session_id)
"""

from __future__ import annotations

import json
import logging
import logging.config
import os
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

# ---------------------------------------------------------------------------
# Contexto correlacional
# ---------------------------------------------------------------------------

_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_session_id: ContextVar[Optional[str]] = ContextVar("session_id", default=None)


def get_request_id() -> Optional[str]:
    """Retorna el request_id actual del contexto."""
    return _request_id.get()


def set_request_id(req_id: Optional[str]) -> None:
    """Establece el request_id en el contexto actual."""
    _request_id.set(req_id)


def get_session_id() -> Optional[str]:
    """Retorna el session_id actual del contexto."""
    return _session_id.get()


def set_session_id(sess_id: Optional[str]) -> None:
    """Establece el session_id en el contexto actual."""
    _session_id.set(sess_id)


@contextmanager
def log_context(
    *, request_id: Optional[str] = None, session_id: Optional[str] = None
) -> Generator[None, None, None]:
    """Context manager para establecer IDs correlacionales temporalmente.

    Ejemplo:
        with log_context(request_id="abc-123"):
            logger.info("Procesando acción")
    """
    prev_req = _request_id.get()
    prev_sess = _session_id.get()
    if request_id is not None:
        _request_id.set(request_id)
    if session_id is not None:
        _session_id.set(session_id)
    try:
        yield
    finally:
        _request_id.set(prev_req)
        _session_id.set(prev_sess)


# ---------------------------------------------------------------------------
# Filtro de contexto
# ---------------------------------------------------------------------------

class ContextFilter(logging.Filter):
    """Inyecta request_id y session_id en cada record de log."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or ""
        record.session_id = get_session_id() or ""
        return True


# ---------------------------------------------------------------------------
# Formatter JSON
# ---------------------------------------------------------------------------

class JSONFormatter(logging.Formatter):
    """Formatter que emite logs como líneas JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "request_id") and record.request_id:
            log_obj["request_id"] = record.request_id
        if hasattr(record, "session_id") and record.session_id:
            log_obj["session_id"] = record.session_id
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        # Campos extra pasados vía extra={}
        for key, value in record.__dict__.items():
            if key not in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "request_id",
                "session_id",
                "message",
                "asctime",
            }:
                log_obj[key] = value
        return json.dumps(log_obj, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Configuración por defecto
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "context": {"()": "poke_player.logging_config.ContextFilter"},
    },
    "formatters": {
        "dev": {
            "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(request_id)s | %(message)s",
            "datefmt": "%H:%M:%S",
        },
        "json": {
            "()": "poke_player.logging_config.JSONFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "level": "DEBUG",
            "formatter": "dev",
            "filters": ["context"],
        },
    },
    "root": {
        "level": "INFO",
        "handlers": ["console"],
    },
    "loggers": {
        "poke_player": {
            "level": "DEBUG",
            "handlers": ["console"],
            "propagate": False,
        },
    },
}


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

_configured = False


def setup_logging(
    *,
    level: Optional[str] = None,
    json_format: bool = False,
    log_file: Optional[str] = None,
) -> None:
    """Configura el logging del proyecto.

    Parameters
    ----------
    level : str, optional
        Nivel de logging (DEBUG, INFO, WARNING, ERROR). Por defecto INFO.
    json_format : bool
        Si True, usa formato JSON. Útil para producción.
    log_file : str, optional
        Si se provee, escribe logs también a ese archivo.
    """
    global _configured
    if _configured:
        return

    effective_level = (level or os.environ.get("POKE_LOG_LEVEL", "INFO")).upper()

    # Crear formatter
    if json_format:
        formatter: logging.Formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(request_id)s | %(message)s",
            datefmt="%H:%M:%S",
        )

    # Crear handler de consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(ContextFilter())

    handlers: List[logging.Handler] = [console_handler]

    # Handler de archivo opcional
    if log_file:
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(ContextFilter())
        handlers.append(file_handler)

    # Configurar root logger
    root = logging.getLogger()
    root.setLevel(getattr(logging, effective_level, logging.INFO))
    # Limpiar handlers previos para evitar duplicados
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)

    # Configurar logger poke_player
    poke_logger = logging.getLogger("poke_player")
    poke_logger.setLevel(getattr(logging, effective_level, logging.DEBUG))

    _configured = True
    poke_logger.debug("Logging configurado", extra={"level": effective_level, "json": json_format})


def get_logger(name: str) -> logging.Logger:
    """Obtiene un logger configurado.

    Si el logging no ha sido configurado explícitamente, retorna un logger
    estándar de Python. Llama setup_logging() al inicio de tu aplicación
    para activar el formato estructurado.
    """
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Métricas simples
# ---------------------------------------------------------------------------

class MetricsCollector:
    """Recopila métricas básicas de rendimiento.

    Ejemplo:
        metrics = MetricsCollector()
        with metrics.timer("frame_advance"):
            emulator.tick()
        metrics.increment("actions_total")
    """

    def __init__(self) -> None:
        self._counters: Dict[str, int] = {}
        self._timers: Dict[str, list[float]] = {}
        self._logger = get_logger("poke_player.metrics")

    def increment(self, name: str, value: int = 1) -> None:
        """Incrementa un contador."""
        self._counters[name] = self._counters.get(name, 0) + value

    @contextmanager
    def timer(self, name: str) -> Generator[None, None, None]:
        """Mide el tiempo de ejecución de un bloque de código."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self._timers.setdefault(name, []).append(elapsed)
            self._logger.debug(
                f"Timer '{name}': {elapsed:.4f}s",
                extra={"metric": name, "elapsed_ms": round(elapsed * 1000, 2)},
            )

    def snapshot(self) -> Dict[str, Any]:
        """Retorna un snapshot actual de las métricas."""
        timers_summary = {}
        for name, values in self._timers.items():
            if values:
                timers_summary[name] = {
                    "count": len(values),
                    "total_ms": round(sum(values) * 1000, 2),
                    "avg_ms": round(sum(values) / len(values) * 1000, 2),
                    "min_ms": round(min(values) * 1000, 2),
                    "max_ms": round(max(values) * 1000, 2),
                }
        return {
            "counters": self._counters.copy(),
            "timers": timers_summary,
        }

    def reset(self) -> None:
        """Limpia todas las métricas acumuladas."""
        self._counters.clear()
        self._timers.clear()


# Singleton global para métricas
_global_metrics = MetricsCollector()


def get_metrics() -> MetricsCollector:
    """Retorna el recolector de métricas global."""
    return _global_metrics
