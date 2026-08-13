"""Backoff y circuit breaker (spec/03-ARCHITECTURE.md "Queue y workers":
"Reintentos con backoff y jitter", "Circuit breaker por destino"; Entrega 2
"Retries, circuit breaker y dead-letter").

Ambos bloques son puros/inyectables (reloj y jitter como parametros) para
poder probarlos sin `time.sleep` real, igual que `hub/ttl.py`.
"""
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


def compute_backoff_seconds(
    attempt: int,
    *,
    base_seconds: float = 1.0,
    max_seconds: float = 60.0,
    jitter_fn: Callable[[], float] = random.random,
) -> float:
    """Backoff exponencial con jitter completo (0..backoff), acotado por
    `max_seconds`. `attempt` empieza en 1 para el primer reintento."""
    exponential = base_seconds * (2 ** max(0, attempt - 1))
    capped = min(exponential, max_seconds)
    return capped * jitter_fn()


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Circuit breaker por destino. `now_fn` es inyectable para tests."""

    failure_threshold: int = 5
    reset_timeout_seconds: float = 30.0
    now_fn: Callable[[], float] = field(default=time.time)

    def __post_init__(self):
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: Optional[float] = None

    @property
    def state(self) -> CircuitState:
        if self._state is CircuitState.OPEN and self._opened_at is not None:
            if self.now_fn() - self._opened_at >= self.reset_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def allow(self) -> bool:
        return self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self.state is CircuitState.HALF_OPEN or self._consecutive_failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = self.now_fn()
