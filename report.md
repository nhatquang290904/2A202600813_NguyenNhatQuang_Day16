# Lab 16 Golden Benchmark Report

## Metadata

- Dataset: `hotpot_golden.json`
- Dataset size: 20 QA examples
- Runtime mode: `mock`
- Total records: 40
- Agents evaluated: `react`, `reflexion`
- Output file: `outputs/golden_run/report.json`

The golden test set was validated successfully with the project `QAExample` schema. The benchmark was run in `mock` mode so the result is deterministic and reproducible during grading. The same pipeline also supports `gemini` mode, but mock mode avoids API quota, network, and provider variability.

## Summary

| Metric | ReAct | Reflexion | Delta |
|---|---:|---:|---:|
| Exact Match | 1.0 | 1.0 | 0.0 |
| Count | 20 | 20 | 0 |
| Avg attempts | 1.0 | 1.0 | 0.0 |
| Avg token estimate | 130.9 | 130.9 | 0.0 |
| Avg latency (ms) | 8 | 8 | 0 |

Both agents reached 100% exact match on the 20-example golden set. In this mock run, every example was answered correctly on the first attempt, so Reflexion did not need to trigger additional reflection attempts.

## ReAct vs Reflexion Comparison

| Aspect | ReAct Agent | Reflexion Agent | Observation |
|---|---|---|---|
| Main behavior | Answers once using the question and context. | Answers, evaluates, reflects on failure, then retries with reflection memory. | Reflexion has a recovery loop, while ReAct is single-pass. |
| Max attempts | 1 | 3 | Reflexion can spend more attempts when the first answer is wrong. |
| Exact Match on golden set | 1.0 | 1.0 | Both agents solved all 20 golden examples in mock mode. |
| Average attempts | 1.0 | 1.0 | No extra attempts were needed in this run. |
| Average token estimate | 130.9 | 130.9 | Token usage is equal because Reflexion did not retry. |
| Average latency | 8 ms | 8 ms | Latency is equal for the same reason. |
| Reflection memory | Not used. | Stores lessons and next strategies after failed attempts. | Useful when the first answer misses a hop or drifts to the wrong entity. |

## Failure Modes

```json
{
  "by_agent": {
    "react": {
      "none": 20
    },
    "reflexion": {
      "none": 20
    }
  },
  "by_mode": {
    "none": 40
  },
  "analyzed_modes": {
    "incomplete_multi_hop": {
      "count": 0,
      "description": "The answer uses the first supporting fact but stops before completing the second hop."
    },
    "entity_drift": {
      "count": 0,
      "description": "The answer switches to a plausible but unsupported entity from nearby context."
    },
    "wrong_final_answer": {
      "count": 0,
      "description": "The answer is grounded poorly or selects the wrong final entity after reasoning."
    }
  }
}
```

No failures appeared in the golden mock benchmark. The report still tracks the three important Reflexion failure categories:

- `incomplete_multi_hop`: the model answers after only the first supporting fact.
- `entity_drift`: the model selects a nearby but unsupported entity.
- `wrong_final_answer`: the model follows the wrong evidence or gives an unsupported final answer.

These categories are most useful in real LLM mode, where mistakes are less deterministic than in the mock runtime.

## Example Outputs

| QID | Agent | Gold Answer | Predicted Answer | Correct | Attempts |
|---|---|---|---|---:|---:|
| `gold1` | ReAct | Beijing | Beijing | true | 1 |
| `gold3` | ReAct | Peruvian sol | Peruvian sol | true | 1 |
| `gold5` | ReAct | C | C | true | 1 |
| `gold11` | Reflexion | Atlantic Ocean | Atlantic Ocean | true | 1 |
| `gold15` | Reflexion | Neil Armstrong | Neil Armstrong | true | 1 |
| `gold20` | Reflexion | 1951 | 1951 | true | 1 |

## Extensions Implemented

- `structured_evaluator`
- `reflection_memory`
- `benchmark_report_json`
- `mock_mode_for_autograding`

## Implementation Notes

The scaffold was completed with explicit Pydantic schemas for `JudgeResult` and `ReflectionEntry`. The Reflexion loop calls the evaluator after each answer, asks the reflector for a correction when the attempt fails, stores that reflection in the trace, and adds the lesson/strategy to reflection memory for the next attempt.

The runtime supports two modes:

- `mock`: deterministic runtime for repeatable benchmark and autograding.
- `gemini`: real LLM runtime using the Actor, Evaluator, and Reflector prompts.

In Gemini mode, evaluator and reflector responses are parsed into structured schema objects. Token usage and latency can also be collected from the real LLM response instead of relying only on fixed mock estimates.

## Discussion

The golden benchmark confirms that the implemented benchmark pipeline can load a held-out Hotpot-style dataset, run both ReAct and Reflexion agents, save JSONL traces, and generate a structured report. The dataset contains 20 examples, so evaluating both agents produces 40 total records.

Reflexion is most useful when the first answer is close but incomplete. For example, a model may identify the first-hop entity but forget to use the second paragraph, drift toward a plausible but unsupported entity, or return a final answer without checking the evidence. In those cases, the evaluator explains the failure and the reflector writes a compact correction for the next attempt.

The tradeoff is cost. Reflexion may require extra actor, evaluator, and reflector calls, so it can use more tokens and add latency compared with ReAct. In this golden mock run, both agents finished in one attempt on every question, so Reflexion did not show an accuracy improvement. Its value appears more clearly on failure-heavy datasets or real LLM runs where the first answer is not always correct.

## Test Results

The latest local checks returned:

```text
Dataset validation: 20 valid QA examples, gold1 to gold20
Unit tests: 1 passed
Golden benchmark: ReAct EM 1.0, Reflexion EM 1.0
```

The latest autograde run on `outputs/golden_run/report.json` returned:

```text
Auto-grade total: 90/100
Flow Score (Core): 70/80
Schema: 30/30
Experiment: 20/30
Analysis: 20/20
Bonus: 20/20
```

The Experiment score is 20/30 because the golden set has 20 examples. The autograder gives the final 10 experiment points only when `num_records >= 100`.
