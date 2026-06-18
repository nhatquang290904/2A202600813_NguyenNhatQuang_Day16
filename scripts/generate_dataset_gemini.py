from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.reflexion_lab.schemas import QAExample


def gemini_generate(prompt: str) -> str:
    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing in .env")

    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    base_url = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    encoded_model = urllib.parse.quote(model, safe="")
    url = f"{base_url}/models/{encoded_model}:generateContent?key={urllib.parse.quote(api_key)}"

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "response_mime_type": "application/json",
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
                break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"Gemini API error {exc.code}: {detail}")
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise last_error from exc
            time.sleep(3 * attempt)
        except urllib.error.URLError as exc:
            last_error = RuntimeError(f"Could not connect to Gemini API: {exc.reason}")
            if attempt == 3:
                raise last_error from exc
            time.sleep(3 * attempt)
    else:
        raise RuntimeError("Gemini API request failed.") from last_error

    try:
        return body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Gemini response: {json.dumps(body, indent=2)}") from exc


def parse_json_array(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))

    if not isinstance(data, list):
        raise ValueError("Gemini response must be a JSON array.")
    return data


def build_prompt(start: int, count: int) -> str:
    end = start + count - 1
    return f"""
Generate exactly {count} diverse multi-hop QA examples for a Reflexion Agent lab.

Return only a valid JSON array. Each item must match this schema exactly:
{{
  "qid": "gemini_{start:03d}",
  "difficulty": "easy" | "medium" | "hard",
  "question": "A multi-hop question answerable from the context",
  "gold_answer": "Concise final answer",
  "context": [
    {{"title": "Source 1", "text": "Evidence for the first hop."}},
    {{"title": "Source 2", "text": "Evidence for the second hop."}}
  ]
}}

Rules:
- Use qid values gemini_{start:03d} through gemini_{end:03d}, in order.
- Each question must require at least two context facts.
- Keep answers short, usually a name, place, object, language, field, or title.
- Make the dataset self-contained. Do not require outside knowledge.
- Use only ASCII text.
- Avoid repeating the same topic too often within this batch.
- Do not include markdown, comments, or wrapper keys.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a multi-hop QA dataset with Gemini.")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--out", default="data/custom_50_gemini.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.count < 1:
        raise ValueError("--count must be positive.")
    if args.batch_size < 1:
        raise ValueError("--batch-size must be positive.")

    out_path = PROJECT_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    examples: list[dict] = []
    for start in range(1, args.count + 1, args.batch_size):
        batch_count = min(args.batch_size, args.count - start + 1)
        raw = gemini_generate(build_prompt(start, batch_count))
        batch = parse_json_array(raw)

        if len(batch) != batch_count:
            raise ValueError(f"Expected {batch_count} examples for batch starting {start}, got {len(batch)}.")

        for offset, item in enumerate(batch):
            item["qid"] = f"gemini_{start + offset:03d}"
            examples.append(QAExample.model_validate(item).model_dump())
        print(f"Generated {len(examples)}/{args.count}")
        out_path.write_text(json.dumps(examples, indent=2, ensure_ascii=False), encoding="utf-8")

    qids = [item["qid"] for item in examples]
    if len(qids) != len(set(qids)):
        raise ValueError("Duplicate qid values found.")

    out_path.write_text(json.dumps(examples, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
