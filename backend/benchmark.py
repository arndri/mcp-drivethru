import asyncio

from agent.evaluation import run_benchmark, summarize


def print_table(rows):
    headers = list(rows[0].keys())
    widths = {h: max(len(h), *(len(str(row[h])) for row in rows)) for h in headers}
    print("  ".join(h.ljust(widths[h]) for h in headers))
    print("  ".join("-" * widths[h] for h in headers))
    for row in rows:
        print("  ".join(str(row[h]).ljust(widths[h]) for h in headers))


if __name__ == "__main__":
    results = asyncio.run(run_benchmark())
    print_table(summarize(results))
    print("\nScenario details:")
    print_table(results)

