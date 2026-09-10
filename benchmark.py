"""Measure concurrent redirect behavior against a running API server.

This script is a repeatable local benchmark, not a substitute for distributed
load-testing tools or production monitoring. Run ``python benchmark.py --help``
to see configurable options.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from math import floor
from statistics import mean, median
from threading import local
from time import perf_counter

import requests


@dataclass(frozen=True)
class RequestResult:
    """Store the observable result of one redirect request."""

    status_code: int | None
    latency_ms: float
    error: str | None = None


_thread_state = local()


def get_thread_session() -> requests.Session:
    """Return one reusable HTTP session per benchmark worker thread."""
    session = getattr(_thread_state, "session", None)
    if session is None:
        session = requests.Session()
        _thread_state.session = session
    return session


def request_redirect(url: str, timeout: float) -> RequestResult:
    """Request one short URL without following its external redirect."""
    started = perf_counter()
    try:
        response = get_thread_session().get(
            url,
            allow_redirects=False,
            timeout=timeout,
        )
        latency_ms = (perf_counter() - started) * 1_000
        return RequestResult(response.status_code, latency_ms)
    except requests.RequestException as exc:
        latency_ms = (perf_counter() - started) * 1_000
        return RequestResult(None, latency_ms, str(exc))


def percentile(sorted_values: list[float], fraction: float) -> float:
    """Calculate an interpolated percentile from sorted numeric values.

    Args:
        sorted_values: Non-empty values arranged from smallest to largest.
        fraction: Desired percentile expressed from ``0.0`` through ``1.0``.

    Returns:
        The interpolated percentile value.

    Raises:
        ValueError: If the list is empty or the fraction is out of range.
    """
    if not sorted_values:
        raise ValueError("percentile requires at least one value")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0.0 and 1.0")

    position = (len(sorted_values) - 1) * fraction
    lower_index = floor(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    weight = position - lower_index
    return (
        sorted_values[lower_index] * (1 - weight)
        + sorted_values[upper_index] * weight
    )


def run_benchmark(
    base_url: str,
    destination: str,
    request_count: int,
    concurrency: int,
    timeout: float,
) -> int:
    """Create a short URL, benchmark redirects, and verify recorded clicks."""
    base_url = base_url.rstrip("/")

    try:
        creation = requests.post(
            f"{base_url}/shorten",
            json={"url": destination},
            timeout=timeout,
        )
        creation.raise_for_status()
        short_code = creation.json()["short_code"]
    except (requests.RequestException, KeyError, ValueError) as exc:
        print(f"Unable to create the benchmark URL: {exc}")
        print("Confirm that the API is running and the base URL is correct.")
        return 1

    redirect_url = f"{base_url}/{short_code}"
    print(
        f"Sending {request_count} requests to {redirect_url} "
        f"with concurrency={concurrency}"
    )

    wall_started = perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(request_redirect, redirect_url, timeout)
            for _ in range(request_count)
        ]
        results = [future.result() for future in as_completed(futures)]
    wall_seconds = perf_counter() - wall_started

    successful = [result for result in results if result.status_code == 307]
    failed = [result for result in results if result.status_code != 307]
    latencies = sorted(result.latency_ms for result in successful)
    status_counts = Counter(
        str(result.status_code) if result.status_code is not None else "error"
        for result in results
    )

    print(f"Completed: {len(results)}")
    print(f"Status counts: {dict(sorted(status_counts.items()))}")
    print(f"Wall time: {wall_seconds:.3f} s")
    print(f"Throughput: {len(results) / wall_seconds:.1f} requests/second")

    if latencies:
        print("Successful redirect latency:")
        print(f"  average: {mean(latencies):.2f} ms")
        print(f"  minimum: {min(latencies):.2f} ms")
        print(f"  median:  {median(latencies):.2f} ms")
        print(f"  p95:     {percentile(latencies, 0.95):.2f} ms")
        print(f"  maximum: {max(latencies):.2f} ms")

    if failed:
        failure_samples = [
            result.error or f"HTTP {result.status_code}" for result in failed[:5]
        ]
        print(f"First failure(s): {failure_samples}")

    try:
        analytics = requests.get(
            f"{base_url}/analytics/{short_code}",
            timeout=timeout,
        )
        analytics.raise_for_status()
        recorded_clicks = int(analytics.json()["clicks"])
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        print(f"Unable to verify the final click count: {exc}")
        return 1

    expected_clicks = len(successful)
    print(f"Recorded clicks: {recorded_clicks} (expected {expected_clicks})")

    click_count_matches = recorded_clicks == expected_clicks
    if not click_count_matches:
        print(
            "Click verification failed. A timed-out request may have reached the "
            "server, or an increment may have been lost."
        )

    return 0 if not failed and click_count_matches else 1


def parse_arguments() -> argparse.Namespace:
    """Parse and validate command-line benchmark configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--destination",
        default="https://example.com/benchmark-target",
    )
    parser.add_argument(
        "--requests",
        dest="request_count",
        type=int,
        default=500,
    )
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=15.0)
    arguments = parser.parse_args()

    if arguments.request_count < 1:
        parser.error("--requests must be at least 1")
    if arguments.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if arguments.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    return arguments


if __name__ == "__main__":
    args = parse_arguments()
    raise SystemExit(
        run_benchmark(
            base_url=args.base_url,
            destination=args.destination,
            request_count=args.request_count,
            concurrency=args.concurrency,
            timeout=args.timeout,
        )
    )
