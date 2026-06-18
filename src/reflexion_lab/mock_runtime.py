from __future__ import annotations
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dotenv import load_dotenv
from .prompts import ACTOR_SYSTEM, EVALUATOR_SYSTEM, REFLECTOR_SYSTEM
from .schemas import QAExample, JudgeResult, ReflectionEntry
from .utils import normalize_answer

load_dotenv()

FIRST_ATTEMPT_WRONG = {"hp2": "London", "hp4": "Atlantic Ocean", "hp6": "Red Sea", "hp8": "Andes"}
FAILURE_MODE_BY_QID = {"hp2": "incomplete_multi_hop", "hp4": "wrong_final_answer", "hp6": "entity_drift", "hp8": "entity_drift"}
_RUNTIME_USAGE = {"tokens": 0, "latency_ms": 0}

def get_runtime_mode() -> str:
    return os.getenv("LLM_MODE", "mock").strip().lower()

def reset_runtime_usage() -> None:
    _RUNTIME_USAGE["tokens"] = 0
    _RUNTIME_USAGE["latency_ms"] = 0

def consume_runtime_usage() -> dict[str, int]:
    usage = dict(_RUNTIME_USAGE)
    reset_runtime_usage()
    return usage

def _record_usage(tokens: int, latency_ms: int) -> None:
    _RUNTIME_USAGE["tokens"] += max(0, tokens)
    _RUNTIME_USAGE["latency_ms"] += max(0, latency_ms)

def _estimate_tokens(*parts: str) -> int:
    text = "\n".join(part for part in parts if part)
    return max(1, round(len(text) / 4))

def _context_text(example: QAExample) -> str:
    return "\n\n".join(f"[{chunk.title}]\n{chunk.text}" for chunk in example.context)

def _gemini_generate(system_prompt: str, user_prompt: str, *, json_mode: bool = False) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it to .env or set LLM_MODE=mock.")

    model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()
    base_url = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    encoded_model = urllib.parse.quote(model, safe="")
    url = f"{base_url}/models/{encoded_model}:generateContent?key={urllib.parse.quote(api_key)}"

    generation_config: dict[str, object] = {"temperature": 0.0}
    if json_mode:
        generation_config["response_mime_type"] = "application/json"

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": generation_config,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    last_error: Exception | None = None
    started = time.perf_counter()
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
                break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"Gemini API error {exc.code}: {detail}")
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise last_error from exc
            time.sleep(2 * attempt)
        except urllib.error.URLError as exc:
            last_error = RuntimeError(f"Could not connect to Gemini API: {exc.reason}")
            if attempt == 3:
                raise last_error from exc
            time.sleep(2 * attempt)
    else:
        raise RuntimeError("Gemini API request failed.") from last_error

    latency_ms = round((time.perf_counter() - started) * 1000)
    body = json.loads(raw)
    usage = body.get("usageMetadata", {})
    token_count = int(usage.get("totalTokenCount") or _estimate_tokens(system_prompt, user_prompt, raw))
    _record_usage(token_count, latency_ms)
    try:
        return body["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Gemini response: {raw}") from exc

def _parse_json_object(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))

def _mock_actor_answer(example: QAExample, attempt_id: int, agent_type: str, reflection_memory: list[str]) -> str:
    if example.qid not in FIRST_ATTEMPT_WRONG:
        return example.gold_answer
    if agent_type == "react":
        return FIRST_ATTEMPT_WRONG[example.qid]
    if attempt_id == 1 and not reflection_memory:
        return FIRST_ATTEMPT_WRONG[example.qid]
    return example.gold_answer

def _mock_evaluator(example: QAExample, answer: str) -> JudgeResult:
    if normalize_answer(example.gold_answer) == normalize_answer(answer):
        return JudgeResult(score=1, reason="Final answer matches the gold answer after normalization.")
    if normalize_answer(answer) == "london":
        return JudgeResult(
            score=0,
            reason="The answer stopped at the birthplace city and never completed the second hop to the river.",
            missing_evidence=["Need to identify the river that flows through London."],
            spurious_claims=[],
        )
    return JudgeResult(
        score=0,
        reason="The final answer selected the wrong second-hop entity.",
        missing_evidence=["Need to ground the answer in the second paragraph."],
        spurious_claims=[answer],
    )

def _mock_reflector(example: QAExample, attempt_id: int, judge: JudgeResult) -> ReflectionEntry:
    strategy = "Do the second hop explicitly: birthplace city -> river through that city." if example.qid == "hp2" else "Verify the final entity against the second paragraph before answering."
    return ReflectionEntry(
        attempt_id=attempt_id,
        failure_reason=judge.reason,
        lesson="A partial first-hop answer is not enough; the final answer must complete all hops.",
        next_strategy=strategy,
    )

def actor_answer(example: QAExample, attempt_id: int, agent_type: str, reflection_memory: list[str]) -> str:
    if get_runtime_mode() != "gemini":
        answer = _mock_actor_answer(example, attempt_id, agent_type, reflection_memory)
        _record_usage(_estimate_tokens(example.question, _context_text(example), *reflection_memory, answer), 5)
        return answer

    user_prompt = f"""Question:
{example.question}

Context:
{_context_text(example)}

Reflection memory:
{json.dumps(reflection_memory, ensure_ascii=False)}

Attempt: {attempt_id}
Agent type: {agent_type}

Return the final answer only."""
    return _gemini_generate(ACTOR_SYSTEM, user_prompt).strip()

def evaluator(example: QAExample, answer: str) -> JudgeResult:
    if get_runtime_mode() != "gemini":
        judge = _mock_evaluator(example, answer)
        _record_usage(_estimate_tokens(example.question, example.gold_answer, answer, judge.reason), 3)
        return judge

    user_prompt = f"""Question:
{example.question}

Gold answer:
{example.gold_answer}

Predicted answer:
{answer}

Context:
{_context_text(example)}

Return only the JSON object."""
    payload = _parse_json_object(_gemini_generate(EVALUATOR_SYSTEM, user_prompt, json_mode=True))
    return JudgeResult.model_validate(payload)

def reflector(example: QAExample, attempt_id: int, judge: JudgeResult) -> ReflectionEntry:
    if get_runtime_mode() != "gemini":
        reflection = _mock_reflector(example, attempt_id, judge)
        _record_usage(_estimate_tokens(example.question, judge.reason, reflection.lesson, reflection.next_strategy), 4)
        return reflection

    user_prompt = f"""Question:
{example.question}

Gold answer:
{example.gold_answer}

Failed attempt id:
{attempt_id}

Evaluator result:
{judge.model_dump_json()}

Context:
{_context_text(example)}

Return only the JSON object."""
    payload = _parse_json_object(_gemini_generate(REFLECTOR_SYSTEM, user_prompt, json_mode=True))
    payload["attempt_id"] = attempt_id
    return ReflectionEntry.model_validate(payload)
