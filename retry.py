import re
import time
import logging

logger = logging.getLogger("retry")

_TRY_AGAIN_IN = re.compile(r"try again in (\d+(?:\.\d+)?)\s*s", re.IGNORECASE)
_RETRY_DELAY_JSON = re.compile(r'"retryDelay"\s*:\s*"(\d+)s?"')

# Hard cap for API-suggested waits (seconds)
_RATE_LIMIT_DELAY_CAP = 120.0


def parse_rate_limit_delay(err_msg: str) -> float | None:
    """Extract suggested retry delay from provider error messages (Groq, Gemini, etc.)."""
    match = _TRY_AGAIN_IN.search(err_msg)
    if match:
        return float(match.group(1)) + 1.0

    match = _RETRY_DELAY_JSON.search(err_msg)
    if match:
        return float(match.group(1)) + 1.0

    return None


def is_rate_limited(err_msg: str) -> bool:
    lower = err_msg.lower()
    return (
        " 429" in err_msg
        or "api error 429" in lower
        or "rate_limit" in lower
        or "rate limit" in lower
    )


def compute_retry_delay(
    err_msg: str,
    attempt: int,
    base_delay: float,
    backoff_factor: float,
    max_delay: float,
) -> float:
    """Compute wait time, honoring API-suggested delays for rate limits."""
    backoff_delay = min(base_delay * (backoff_factor ** (attempt - 1)), max_delay)
    api_delay = parse_rate_limit_delay(err_msg)

    if api_delay is not None:
        return min(max(backoff_delay, api_delay), _RATE_LIMIT_DELAY_CAP)
    if is_rate_limited(err_msg):
        return backoff_delay
    return backoff_delay


def with_retry(fn, retry_config: dict, label: str = "Operation"):
    """
    执行带有指数退避 (Exponential Backoff) 机制的操作重试。
    429 速率限制时优先采用 API 返回的建议等待时间。
    """
    max_retries = retry_config["max_retries"]
    base_delay = retry_config["base_delay"]
    backoff_factor = retry_config["backoff_factor"]
    max_delay = retry_config["max_delay"]

    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            attempt += 1
            err_msg = str(e)

            is_unauthorized = any(
                x in err_msg.lower()
                for x in ["401", "unauthorized", "invalid api key", "forbidden", "403"]
            )
            if is_unauthorized:
                logger.error(
                    f"[{label}] Non-retryable authentication error on attempt {attempt}: {err_msg}"
                )
                raise e

            if attempt > max_retries:
                if "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
                    logger.error(
                        f"[{label}] 网络超时：在 {max_retries} 次重试后仍然无法连接。"
                        f"请检查网络连接或稍后重试。错误: {err_msg}"
                    )
                elif is_rate_limited(err_msg):
                    logger.error(
                        f"[{label}] 速率限制：在 {max_retries} 次重试后仍然被限流。"
                        f"请减小 batch_size 或升级 API 配额。错误: {err_msg}"
                    )
                else:
                    logger.error(
                        f"[{label}] 操作失败：在 {max_retries} 次重试后仍然失败。错误: {err_msg}"
                    )
                raise e

            delay = compute_retry_delay(
                err_msg, attempt, base_delay, backoff_factor, max_delay
            )
            if is_rate_limited(err_msg):
                logger.warning(
                    f"[{label}] 第 {attempt} 次尝试触发速率限制，"
                    f"等待 {delay:.1f} 秒后重试..."
                )
            else:
                logger.warning(
                    f"[{label}] 第 {attempt} 次尝试失败，{delay:.1f} 秒后重试... 错误: {err_msg}"
                )
            time.sleep(delay)
