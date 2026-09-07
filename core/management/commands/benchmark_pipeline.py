import time

import numpy as np
from django.core.management.base import BaseCommand
from tabulate import tabulate

from core.vision import normalizer, preprocess


class Command(BaseCommand):
    help = "Runs throughput/latency micro-benchmarks across each pipeline layer and prints a summary table."

    def add_arguments(self, parser):
        parser.add_argument("--iterations", type=int, default=200, help="Iterations per benchmark.")

    def handle(self, *args, **options):
        n = options["iterations"]
        rows = []

        rows.append(self._bench_normalizer(n))
        rows.append(self._bench_preprocess(n))
        rows.append(self._bench_fuzzy_matcher(n))

        self.stdout.write(
            tabulate(rows, headers=["Stage", "Iterations", "Total (s)", "Per-op", "Throughput/sec"], tablefmt="github")
        )

    def _bench_normalizer(self, n):
        samples = ["uma 123-aa", "UBB456C", "uma1238a", "u m a 9 8 7 z z"] * (n // 4 + 1)
        samples = samples[:n]
        start = time.perf_counter()
        for s in samples:
            normalizer.normalize_plate(s)
        elapsed = time.perf_counter() - start
        return self._row("Positional Syntax Normalizer", n, elapsed)

    def _bench_preprocess(self, n):
        # Synthetic 720x1280 BGR frame -- representative of a field-camera photo
        frame = (np.random.rand(720, 1280, 3) * 255).astype(np.uint8)
        start = time.perf_counter()
        for _ in range(n):
            preprocess.preprocess_pipeline(frame)
        elapsed = time.perf_counter() - start
        return self._row("CLAHE & Preprocessing", n, elapsed)

    def _bench_fuzzy_matcher(self, n):
        from rapidfuzz import fuzz

        registry = [f"U{chr(65 + i % 26)}{chr(65 + (i * 3) % 26)}{100 + i}AA" for i in range(500)]
        query = "UMA123AA"
        start = time.perf_counter()
        for _ in range(n):
            for candidate in registry:
                fuzz.ratio(query, candidate)
        elapsed = time.perf_counter() - start
        return self._row(f"RapidFuzz Bounded Matcher (registry={len(registry)})", n, elapsed)

    @staticmethod
    def _row(name, n, elapsed):
        per_op = elapsed / n if n else 0
        throughput = n / elapsed if elapsed > 0 else float("inf")
        return [name, n, f"{elapsed:.4f}", f"{per_op * 1000:.3f} ms", f"{throughput:,.0f}"]
