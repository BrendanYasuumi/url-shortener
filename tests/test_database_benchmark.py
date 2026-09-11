"""Tests for the isolated SQLite benchmark's setup and measurements."""

from pathlib import Path

import pytest

from app.database import database_connection
from app.utils import decode_base62, encode_base62
from database_benchmark import (
    LatencyStats,
    build_lookup_codes,
    measure_indexed_lookups,
    populate_database,
    read_query_plan,
    run_database_benchmark,
)


def test_latency_stats_convert_nanoseconds_and_compute_percentiles() -> None:
    """Reported statistics should use milliseconds and stable interpolation."""
    statistics = LatencyStats.from_nanoseconds(
        [1_000_000, 2_000_000, 3_000_000, 4_000_000]
    )

    assert statistics.samples == 4
    assert statistics.average_ms == 2.5
    assert statistics.minimum_ms == 1.0
    assert statistics.median_ms == 2.5
    assert statistics.p95_ms == pytest.approx(3.85)
    assert statistics.p99_ms == pytest.approx(3.97)
    assert statistics.maximum_ms == 4.0


def test_latency_stats_reject_empty_samples() -> None:
    """An empty timing set cannot produce meaningful statistics."""
    with pytest.raises(ValueError, match="at least one"):
        LatencyStats.from_nanoseconds([])


def test_populated_database_uses_index_for_known_and_missing_codes(
    tmp_path: Path,
) -> None:
    """The synthetic dataset should exercise the real indexed lookup function."""
    database_path = tmp_path / "indexed.db"
    populate_database(database_path, row_count=100, batch_size=25)

    with database_connection(database_path) as connection:
        total = connection.execute("SELECT COUNT(*) FROM urls").fetchone()[0]
        plan = read_query_plan(connection, encode_base62(50))
        hit_stats = measure_indexed_lookups(
            connection,
            [encode_base62(1), encode_base62(50), encode_base62(100)],
            should_exist=True,
        )
        miss_stats = measure_indexed_lookups(
            connection,
            [encode_base62(101), encode_base62(102)],
            should_exist=False,
        )

    assert total == 100
    assert "USING INDEX" in plan
    assert hit_stats.samples == 3
    assert miss_stats.samples == 2


def test_lookup_code_generation_is_deterministic_and_separates_misses() -> None:
    """Benchmark workloads should be repeatable and have known outcomes."""
    hits = build_lookup_codes(100, 10, seed=42, existing=True)
    repeated_hits = build_lookup_codes(100, 10, seed=42, existing=True)
    misses = build_lookup_codes(100, 10, seed=42, existing=False)

    assert hits == repeated_hits
    assert all(1 <= decode_base62(code) <= 100 for code in hits)
    assert not set(hits).intersection(misses)


def test_small_database_benchmark_runs_every_measurement(tmp_path: Path) -> None:
    """The orchestrator should produce samples without requiring a large fixture."""
    result = run_database_benchmark(
        tmp_path / "complete.db",
        row_count=100,
        lookup_count=10,
        warmup_count=5,
        connection_count=3,
        transaction_count=4,
        seed=42,
    )

    assert result.rows == 100
    assert result.database_size_bytes > 0
    assert "USING INDEX" in result.query_plan
    assert result.successful_lookups.samples == 10
    assert result.missing_lookups.samples == 10
    assert result.connection_lifecycles.samples == 3
    assert result.redirect_transactions.samples == 4


def test_database_benchmark_refuses_to_overwrite_existing_file(
    tmp_path: Path,
) -> None:
    """An explicit benchmark path must never replace existing user data."""
    database_path = tmp_path / "existing.db"
    original_contents = b"do not replace"
    database_path.write_bytes(original_contents)

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        run_database_benchmark(
            database_path,
            row_count=10,
            lookup_count=2,
            warmup_count=1,
            connection_count=1,
            transaction_count=1,
            seed=42,
        )

    assert database_path.read_bytes() == original_contents
