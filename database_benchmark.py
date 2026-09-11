"""Benchmark indexed SQLite operations without HTTP framework overhead.

The existing ``benchmark.py`` measures the complete redirect endpoint. This
script isolates database work so SQL lookup latency is not confused with HTTP
routing, middleware, thread scheduling, or response serialization.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
import platform
import random
import sqlite3
from statistics import mean, median
import tempfile
from time import perf_counter_ns

from app.database import (
    create_connection,
    database_connection,
    find_url_by_short_code,
    initialize_database,
    record_click_and_get_url,
)
from app.utils import MAX_BASE62_VALUE, encode_base62
from benchmark import percentile


NANOSECONDS_PER_MILLISECOND = 1_000_000


@dataclass(frozen=True)
class LatencyStats:
    """Summary statistics for one group of timed operations."""

    samples: int
    average_ms: float
    minimum_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    maximum_ms: float

    @classmethod
    def from_nanoseconds(cls, values: list[int]) -> "LatencyStats":
        """Calculate millisecond statistics from non-empty nanosecond samples."""
        if not values:
            raise ValueError("latency statistics require at least one sample")

        milliseconds = sorted(
            value / NANOSECONDS_PER_MILLISECOND for value in values
        )
        return cls(
            samples=len(milliseconds),
            average_ms=mean(milliseconds),
            minimum_ms=milliseconds[0],
            median_ms=median(milliseconds),
            p95_ms=percentile(milliseconds, 0.95),
            p99_ms=percentile(milliseconds, 0.99),
            maximum_ms=milliseconds[-1],
        )


@dataclass(frozen=True)
class DatabaseBenchmarkResult:
    """All measurements and metadata produced by one benchmark run."""

    rows: int
    database_size_bytes: int
    query_plan: str
    successful_lookups: LatencyStats
    missing_lookups: LatencyStats
    connection_lifecycles: LatencyStats
    redirect_transactions: LatencyStats


def populate_database(
    database_path: Path,
    row_count: int,
    batch_size: int = 1_000,
) -> None:
    """Create the schema and insert deterministic benchmark URL rows.

    Explicit integer IDs produce the same Base62 values as the application's
    normal insert-then-encode flow. Batching controls temporary memory use while
    one surrounding transaction keeps setup time outside the measurements.
    """
    if row_count < 1:
        raise ValueError("row_count must be at least 1")
    if row_count > MAX_BASE62_VALUE:
        raise ValueError("row_count exceeds six-character Base62 capacity")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    initialize_database(database_path)
    with database_connection(database_path) as connection:
        for first_id in range(1, row_count + 1, batch_size):
            final_id = min(first_id + batch_size, row_count + 1)
            rows = [
                (
                    url_id,
                    f"https://example.com/benchmark/{url_id}",
                    encode_base62(url_id),
                )
                for url_id in range(first_id, final_id)
            ]
            connection.executemany(
                """
                INSERT INTO urls (id, original_url, short_code)
                VALUES (?, ?, ?)
                """,
                rows,
            )


def build_lookup_codes(
    row_count: int,
    sample_count: int,
    seed: int,
    *,
    existing: bool,
) -> list[str]:
    """Precompute deterministic codes so random generation is not timed."""
    if row_count < 1:
        raise ValueError("row_count must be at least 1")
    if sample_count < 1:
        raise ValueError("sample_count must be at least 1")

    randomizer = random.Random(seed)
    if existing:
        identifiers = (
            randomizer.randint(1, row_count) for _ in range(sample_count)
        )
    else:
        available_missing_values = MAX_BASE62_VALUE - row_count
        if available_missing_values < 1:
            raise ValueError("dataset leaves no unused Base62 codes")
        identifiers = (
            randomizer.randint(row_count + 1, MAX_BASE62_VALUE)
            for _ in range(sample_count)
        )
    return [encode_base62(identifier) for identifier in identifiers]


def warm_up_lookups(
    connection: sqlite3.Connection,
    short_codes: list[str],
) -> None:
    """Execute untimed reads to separate warm-cache results from startup cost."""
    for short_code in short_codes:
        row = find_url_by_short_code(connection, short_code)
        if row is None:
            raise RuntimeError("warm-up code was unexpectedly absent")


def measure_indexed_lookups(
    connection: sqlite3.Connection,
    short_codes: list[str],
    *,
    should_exist: bool,
) -> LatencyStats:
    """Measure application lookup calls using one already-open connection."""
    elapsed_values: list[int] = []
    for short_code in short_codes:
        started = perf_counter_ns()
        row = find_url_by_short_code(connection, short_code)
        elapsed_values.append(perf_counter_ns() - started)

        if (row is not None) != should_exist:
            expectation = "present" if should_exist else "absent"
            raise RuntimeError(f"benchmark code should have been {expectation}")

    return LatencyStats.from_nanoseconds(elapsed_values)


def measure_connection_lifecycles(
    database_path: Path,
    sample_count: int,
) -> LatencyStats:
    """Measure configured SQLite connection creation and cleanup."""
    elapsed_values: list[int] = []
    for _ in range(sample_count):
        started = perf_counter_ns()
        connection = create_connection(database_path)
        connection.close()
        elapsed_values.append(perf_counter_ns() - started)
    return LatencyStats.from_nanoseconds(elapsed_values)


def measure_redirect_transactions(
    database_path: Path,
    short_codes: list[str],
) -> LatencyStats:
    """Measure the redirect's connection, atomic update, read, and commit."""
    elapsed_values: list[int] = []
    for short_code in short_codes:
        started = perf_counter_ns()
        with database_connection(database_path) as connection:
            destination = record_click_and_get_url(connection, short_code)
            if destination is None:
                raise RuntimeError("redirect benchmark code was unexpectedly absent")
        elapsed_values.append(perf_counter_ns() - started)
    return LatencyStats.from_nanoseconds(elapsed_values)


def read_query_plan(connection: sqlite3.Connection, short_code: str) -> str:
    """Return SQLite's execution plan for the application's lookup query."""
    rows = connection.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM urls WHERE short_code = ?",
        (short_code,),
    ).fetchall()
    return " | ".join(str(row["detail"]) for row in rows)


def run_database_benchmark(
    database_path: Path,
    row_count: int,
    lookup_count: int,
    warmup_count: int,
    connection_count: int,
    transaction_count: int,
    seed: int,
) -> DatabaseBenchmarkResult:
    """Populate a new database and execute every isolated measurement."""
    if database_path.exists():
        raise FileExistsError(
            f"refusing to overwrite existing database: {database_path}"
        )
    if warmup_count < 0:
        raise ValueError("warmup_count must not be negative")
    if connection_count < 1:
        raise ValueError("connection_count must be at least 1")
    if transaction_count < 1:
        raise ValueError("transaction_count must be at least 1")

    print(f"Preparing {row_count:,} rows at {database_path} ...")
    populate_database(database_path, row_count)

    hit_codes = build_lookup_codes(row_count, lookup_count, seed, existing=True)
    miss_codes = build_lookup_codes(
        row_count,
        lookup_count,
        seed + 1,
        existing=False,
    )
    warmup_codes = build_lookup_codes(
        row_count,
        warmup_count,
        seed + 2,
        existing=True,
    ) if warmup_count else []
    transaction_codes = build_lookup_codes(
        row_count,
        transaction_count,
        seed + 3,
        existing=True,
    )

    with database_connection(database_path) as connection:
        warm_up_lookups(connection, warmup_codes)
        query_plan = read_query_plan(connection, hit_codes[0])
        successful_lookups = measure_indexed_lookups(
            connection,
            hit_codes,
            should_exist=True,
        )
        missing_lookups = measure_indexed_lookups(
            connection,
            miss_codes,
            should_exist=False,
        )

    connection_lifecycles = measure_connection_lifecycles(
        database_path,
        connection_count,
    )
    redirect_transactions = measure_redirect_transactions(
        database_path,
        transaction_codes,
    )

    return DatabaseBenchmarkResult(
        rows=row_count,
        database_size_bytes=database_path.stat().st_size,
        query_plan=query_plan,
        successful_lookups=successful_lookups,
        missing_lookups=missing_lookups,
        connection_lifecycles=connection_lifecycles,
        redirect_transactions=redirect_transactions,
    )


def print_latency_stats(label: str, statistics: LatencyStats) -> None:
    """Print one consistently formatted latency section."""
    print(f"\n{label} ({statistics.samples:,} samples)")
    print(f"  average: {statistics.average_ms:.4f} ms")
    print(f"  minimum: {statistics.minimum_ms:.4f} ms")
    print(f"  median:  {statistics.median_ms:.4f} ms")
    print(f"  p95:     {statistics.p95_ms:.4f} ms")
    print(f"  p99:     {statistics.p99_ms:.4f} ms")
    print(f"  maximum: {statistics.maximum_ms:.4f} ms")


def print_report(result: DatabaseBenchmarkResult) -> None:
    """Print environment metadata and all benchmark measurements."""
    print("\nSQLite database benchmark")
    print(f"  platform: {platform.platform()}")
    print(f"  Python:   {platform.python_version()}")
    print(f"  SQLite:   {sqlite3.sqlite_version}")
    print(f"  rows:     {result.rows:,}")
    print(f"  DB size:  {result.database_size_bytes / (1024 * 1024):.2f} MiB")
    print(f"  plan:     {result.query_plan}")

    print_latency_stats("Warm-cache indexed lookup (found)", result.successful_lookups)
    print_latency_stats("Warm-cache indexed lookup (missing)", result.missing_lookups)
    print_latency_stats("Connection open and close", result.connection_lifecycles)
    print_latency_stats(
        "Redirect DB transaction (connect, update, select, commit)",
        result.redirect_transactions,
    )

    target_ms = 5.0
    average_ms = result.successful_lookups.average_ms
    comparison = "meets" if average_ms < target_ms else "does not meet"
    print(
        f"\nThe indexed lookup average {comparison} the local "
        f"< {target_ms:.1f} ms target."
    )
    print("Timing is informational; hardware-dependent thresholds do not fail CI.")


def parse_arguments() -> argparse.Namespace:
    """Parse and validate command-line benchmark settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        help="Fresh output path to retain; omitted uses a temporary database",
    )
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--lookups", type=int, default=10_000)
    parser.add_argument("--warmup", type=int, default=1_000)
    parser.add_argument("--connections", type=int, default=1_000)
    parser.add_argument("--transactions", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=20_260_910)
    arguments = parser.parse_args()

    if arguments.rows < 1:
        parser.error("--rows must be at least 1")
    if arguments.rows > MAX_BASE62_VALUE:
        parser.error("--rows exceeds six-character Base62 capacity")
    if arguments.lookups < 1:
        parser.error("--lookups must be at least 1")
    if arguments.warmup < 0:
        parser.error("--warmup must not be negative")
    if arguments.connections < 1:
        parser.error("--connections must be at least 1")
    if arguments.transactions < 1:
        parser.error("--transactions must be at least 1")
    if arguments.database is not None and arguments.database.exists():
        parser.error("--database must point to a path that does not exist")
    return arguments


def main() -> int:
    """Run against an explicit fresh path or an automatically cleaned file."""
    arguments = parse_arguments()
    if arguments.database is not None:
        result = run_database_benchmark(
            arguments.database,
            arguments.rows,
            arguments.lookups,
            arguments.warmup,
            arguments.connections,
            arguments.transactions,
            arguments.seed,
        )
        print_report(result)
        print(f"Database retained at: {arguments.database}")
        return 0

    with tempfile.TemporaryDirectory(prefix="url-shortener-benchmark-") as directory:
        result = run_database_benchmark(
            Path(directory) / "benchmark.db",
            arguments.rows,
            arguments.lookups,
            arguments.warmup,
            arguments.connections,
            arguments.transactions,
            arguments.seed,
        )
        print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
