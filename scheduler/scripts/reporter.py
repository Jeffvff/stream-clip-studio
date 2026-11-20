import csv
from collections import Counter


def show(rows):
    for row in rows:
        error = f" | {row['error']}" if row.get("error") else ""
        print(f"{row['status']:12} {row['local']} | {row['name']}{error}")
    print("Total:", len(rows), dict(Counter(row["status"] for row in rows)))


def to_csv(path, rows):
    fields = ["status", "name", "path", "hash", "local", "zone", "utc", "attempts", "error",
              "remote_url", "evidence"]
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            safe = {key: ("'" + val if isinstance(val, str) and val.startswith(("=", "+", "-", "@")) else val)
                    for key, val in row.items()}
            writer.writerow(safe)
