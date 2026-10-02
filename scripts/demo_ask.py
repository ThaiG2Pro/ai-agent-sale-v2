"""Why this exists: a one-line demo client for README GIFs and live demos.
What it does: POSTs one question to the local API and prints the grounded answer,
the decline flag, and the SKUs it cited. Usage: python3 scripts/demo_ask.py "<question>"
(set API_URL to target another host/port).
"""

import json
import os
import sys
import urllib.request

API_URL = os.environ.get("API_URL", "http://localhost:8000")


def main() -> None:
    question = " ".join(sys.argv[1:]) or "Điện thoại nào pin tốt dưới 10 triệu?"
    req = urllib.request.Request(
        f"{API_URL}/query",
        data=json.dumps({"query": question}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        d = json.load(resp)
    print(d["answer"].strip()[:600])
    print()
    print(
        f"declined={d['declined']}  citations={len(d['citations'])}  "
        f"best_similarity={d['best_similarity']:.2f}  model={d['model_used']}"
    )
    for c in d["citations"][:3]:
        print(f" - {c['sku']}: {c['name'][:60]}")


if __name__ == "__main__":
    main()
