from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError as PydanticValidationError

from .config import RetryConfig
from .model_client import StructuredModelClient
from .types import ModelCallRecord


T = TypeVar("T", bound=BaseModel)


class RouteDeadlineExceeded(TimeoutError):
    pass


class StageCallTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class RouteDeadline:
    expires_at: float

    @classmethod
    def after_ms(cls, timeout_ms: int) -> "RouteDeadline":
        return cls(time.monotonic() + timeout_ms / 1000)

    def remaining_seconds(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())


class ModelCallGovernor:
    def __init__(
        self,
        *,
        global_concurrency: int,
        retry: RetryConfig,
    ) -> None:
        self._semaphore = asyncio.Semaphore(global_concurrency)
        self.retry = retry

    async def structured(
        self,
        client: StructuredModelClient,
        *,
        stage: str,
        timeout_ms: int,
        deadline: RouteDeadline,
        records: list[ModelCallRecord],
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        started = time.perf_counter()
        attempts = 0
        last_error: Exception | None = None

        while attempts <= self.retry.max_retries:
            attempts += 1
            remaining = deadline.remaining_seconds()
            if remaining <= 0:
                error = RouteDeadlineExceeded("route deadline exhausted")
                records.append(
                    _record(stage, started, attempts, "timeout", error),
                )
                raise error
            call_timeout = min(timeout_ms / 1000, remaining)
            try:
                async with self._semaphore:
                    async with asyncio.timeout(call_timeout):
                        result = await client.structured(
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            response_model=response_model,
                        )
            except asyncio.CancelledError:
                raise
            except TimeoutError as exc:
                last_error = StageCallTimeout(
                    f"{stage} timed out after {call_timeout:.3f}s",
                )
                if attempts > self.retry.max_retries:
                    records.append(
                        _record(stage, started, attempts, "timeout", last_error),
                    )
                    raise last_error from exc
            except Exception as exc:
                last_error = exc
                if not _is_retryable(exc) or attempts > self.retry.max_retries:
                    records.append(
                        _record(stage, started, attempts, "error", exc),
                    )
                    raise
            else:
                records.append(
                    _record(stage, started, attempts, "ok", None),
                )
                return result

            await self._backoff(attempts, deadline)

        assert last_error is not None
        raise last_error

    async def _backoff(self, attempts: int, deadline: RouteDeadline) -> None:
        base = self.retry.base_backoff_ms / 1000 * (2 ** (attempts - 1))
        jitter = base * self.retry.jitter_ratio * random.random()
        delay = min(base + jitter, deadline.remaining_seconds())
        if delay > 0:
            await asyncio.sleep(delay)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            PydanticValidationError,
        ),
    ):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True
    return isinstance(status_code, int) and 500 <= status_code < 600


def _record(
    stage: str,
    started: float,
    attempts: int,
    status: str,
    error: Exception | None,
) -> ModelCallRecord:
    return ModelCallRecord(
        stage=stage,
        duration_ms=(time.perf_counter() - started) * 1000,
        attempts=attempts,
        status=status,
        error=str(error) if error else None,
    )

