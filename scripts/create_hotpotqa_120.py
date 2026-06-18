from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.reflexion_lab.schemas import QAExample


def fetch_hotpotqa_batch(offset: int, length: int) -> list[dict]:
    query = urllib.parse.urlencode(
        {
            "dataset": "hotpotqa/hotpot_qa",
            "config": "distractor",
            "split": "validation",
            "offset": offset,
            "length": length,
        }
    )
    url = f"https://datasets-server.huggingface.co/rows?{query}"
    with urllib.request.urlopen(url, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [item["row"] for item in payload["rows"]]


def fetch_hotpotqa_rows(count: int) -> list[dict]:
    rows: list[dict] = []
    while len(rows) < count:
        batch_len = min(100, count - len(rows))
        rows.extend(fetch_hotpotqa_batch(len(rows), batch_len))
    return rows


def convert_row(row: dict) -> dict:
    titles = row["context"]["title"]
    sentence_groups = row["context"]["sentences"]
    context = [
        {"title": title, "text": " ".join(sentences)}
        for title, sentences in zip(titles, sentence_groups)
    ]
    return {
        "qid": row["id"],
        "difficulty": row["level"],
        "question": row["question"],
        "gold_answer": row["answer"],
        "context": context,
    }


def main() -> None:
    examples = [QAExample.model_validate(convert_row(row)).model_dump() for row in fetch_hotpotqa_rows(120)]
    out_path = PROJECT_ROOT / "data" / "hotpotqa_120.json"
    out_path.write_text(json.dumps(examples, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {out_path}")
    print(f"Examples: {len(examples)}")


if __name__ == "__main__":
    main()
