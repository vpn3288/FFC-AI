"""
Enhanced HTTP client with connection pooling, retry logic, and timeout management.
Designed for stable third-party API integration.
"""

from __future__ import annotations

import json
import os
import random
import time
from http.client import HTTPException
from typing import Any
from urllib import error, request
from urllib.error import URLError


class RetryConfig:
    """Configuration for retry behavior"""

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 30.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ):
        self.max_attempts = max(1, max_attempts)
        self.base_delay_seconds = max(0.1, base_delay_seconds)
        self.max_delay_seconds = max_delay_seconds
        self.exponential_base = exponential_base
        self.jitter = jitter

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt with exponential backoff and jitter"""
        if attempt <= 0:
            return 0.0

        delay = self.base_delay_seconds * (self.exponential_base ** (attempt - 1))
        delay = min(delay, self.max_delay_seconds)

        if self.jitter:
            jitter_range = delay * 0.3
            delay = delay - jitter_range + (random.random() * 2 * jitter_range)

        return max(0.0, delay)


TRANSIENT_HTTP_ERRORS = (
    URLError,
    TimeoutError,
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionRefusedError,
    BrokenPipeError,
)

TRANSIENT_HTTP_CODES = {408, 429, 500, 502, 503, 504}


def is_transient_error(exc: BaseException, response_code: int | None = None) -> bool:
    """Check if an error is transient and worth retrying"""
    if isinstance(exc, TRANSIENT_HTTP_ERRORS):
        return True

    if response_code and response_code in TRANSIENT_HTTP_CODES:
        return True

    if isinstance(exc, HTTPException):
        error_str = str(exc).lower()
        return any(marker in error_str for marker in (
            "timeout", "timed out", "connection reset", "connection closed",
            "connection aborted", "temporary", "retry", "overload",
        ))

    return False


class EnhancedHTTPClient:
    """Enhanced HTTP client with retry and stability features"""

    def __init__(
        self,
        default_timeout: int = 30,
        retry_config: RetryConfig | None = None,
        user_agent: str = "FFC-AI-Client/1.0",
    ):
        self.default_timeout = default_timeout
        self.retry_config = retry_config or RetryConfig()
        self.user_agent = user_agent

    def _build_request(
        self,
        url: str,
        method: str = "POST",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> request.Request:
        """Build urllib Request object with proper headers"""
        req_headers = {
            "User-Agent": self.user_agent,
        }

        if headers:
            req_headers.update(headers)

        if data and "Content-Type" not in req_headers:
            req_headers["Content-Type"] = "application/json; charset=utf-8"

        return request.Request(url, data=data, headers=req_headers, method=method)

    def call(
        self,
        url: str,
        method: str = "POST",
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
        headers: dict[str, str] | None = None,
        retry: bool = True,
    ) -> dict[str, Any]:
        """
        Make HTTP request with automatic retry on transient errors

        Args:
            url: Target URL
            method: HTTP method (POST, GET, etc.)
            payload: JSON payload to send
            timeout: Request timeout in seconds
            headers: Additional headers
            retry: Enable retry on transient errors

        Returns:
            Parsed JSON response

        Raises:
            RuntimeError: On permanent errors or exhausted retries
        """
        actual_timeout = timeout if timeout is not None else self.default_timeout
        data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload else None

        last_exception: Exception | None = None
        max_attempts = self.retry_config.max_attempts if retry else 1

        for attempt in range(1, max_attempts + 1):
            try:
                req = self._build_request(url, method, data, headers)
                response = request.urlopen(req, timeout=actual_timeout)
                response_data = response.read().decode("utf-8")
                return json.loads(response_data)

            except error.HTTPError as exc:
                last_exception = exc
                if not retry or attempt >= max_attempts:
                    raise RuntimeError(f"HTTP {exc.code} error: {exc.reason}") from exc

                if not is_transient_error(exc, exc.code):
                    raise RuntimeError(f"HTTP {exc.code} error (permanent): {exc.reason}") from exc

                delay = self.retry_config.get_delay(attempt)
                if delay > 0:
                    time.sleep(delay)
                continue

            except (URLError, TimeoutError, HTTPException, OSError) as exc:
                last_exception = exc
                if not retry or attempt >= max_attempts:
                    raise RuntimeError(f"Request failed: {exc}") from exc

                if not is_transient_error(exc):
                    raise RuntimeError(f"Request failed (permanent): {exc}") from exc

                delay = self.retry_config.get_delay(attempt)
                if delay > 0:
                    time.sleep(delay)
                continue

        raise RuntimeError(f"Request failed after {max_attempts} attempts") from last_exception


class TelegramHTTPClient(EnhancedHTTPClient):
    """Specialized HTTP client for Telegram Bot API with optimized retry"""

    def __init__(self, token: str, api_base: str = "https://api.telegram.org"):
        telegram_retry = RetryConfig(
            max_attempts=int(os.environ.get("TELEGRAM_HTTP_RETRY_ATTEMPTS", "4")),
            base_delay_seconds=float(os.environ.get("TELEGRAM_HTTP_RETRY_BASE_DELAY", "0.5")),
            max_delay_seconds=float(os.environ.get("TELEGRAM_HTTP_RETRY_MAX_DELAY", "10.0")),
            exponential_base=2.0,
            jitter=True,
        )
        super().__init__(
            default_timeout=30,
            retry_config=telegram_retry,
            user_agent="FFC-AI-Telegram-Bot/1.0",
        )
        self.token = token
        self.api_base = api_base.rstrip("/")

    def _url(self, method: str) -> str:
        """Build Telegram API URL"""
        return f"{self.api_base}/bot{self.token}/{method}"

    def call_telegram(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """
        Call Telegram Bot API method with automatic retry

        Args:
            method: Telegram API method name
            payload: Request payload
            timeout: Custom timeout

        Returns:
            API response data

        Raises:
            RuntimeError: On API errors
        """
        response = self.call(
            self._url(method),
            method="POST",
            payload=payload,
            timeout=timeout,
        )

        if not response.get("ok"):
            error_msg = response.get("description", "unknown_telegram_error")
            raise RuntimeError(f"telegram_{method}_failed: {error_msg}")

        return response


def create_http_client(
    client_type: str = "default",
    **kwargs: Any,
) -> EnhancedHTTPClient:
    """
    Factory function to create appropriate HTTP client

    Args:
        client_type: Type of client ("default", "telegram", etc.)
        **kwargs: Client-specific arguments

    Returns:
        Configured HTTP client instance
    """
    if client_type == "telegram":
        return TelegramHTTPClient(
            token=kwargs.get("token", ""),
            api_base=kwargs.get("api_base", "https://api.telegram.org"),
        )

    retry_config = RetryConfig(
        max_attempts=kwargs.get("max_attempts", 3),
        base_delay_seconds=kwargs.get("base_delay_seconds", 1.0),
        max_delay_seconds=kwargs.get("max_delay_seconds", 30.0),
    )

    return EnhancedHTTPClient(
        default_timeout=kwargs.get("timeout", 30),
        retry_config=retry_config,
        user_agent=kwargs.get("user_agent", "FFC-AI-Client/1.0"),
    )
