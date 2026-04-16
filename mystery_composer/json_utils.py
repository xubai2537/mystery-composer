"""Robust JSON parsing for LLM responses."""

import json
import re


def parse_json_response(content: str) -> dict:
    """Parse JSON from LLM response, handling common formatting issues.

    Handles: markdown code blocks, trailing commas, extra text before/after JSON, etc.
    """
    content = content.strip()

    # Strip markdown code blocks (```json ... ``` or ``` ... ```)
    if "```" in content:
        # Find content between first ``` and last ```
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", content, re.DOTALL)
        if match:
            content = match.group(1).strip()

    # Try direct parse first
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON object from surrounding text
    # Find the outermost { ... }
    brace_start = content.find("{")
    brace_end = content.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        json_str = content[brace_start:brace_end + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        # Fix trailing commas before } or ]
        fixed = re.sub(r",\s*([}\]])", r"\1", json_str)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError(
        f"Failed to parse JSON from LLM response. Raw content:\n{content[:500]}",
        content, 0
    )


def safe_parse_json(content: str, llm=None, prompt: str = "", max_retries: int = 2) -> dict:
    """Parse JSON with optional LLM retry on failure."""
    try:
        return parse_json_response(content)
    except json.JSONDecodeError as e:
        if llm and prompt and max_retries > 0:
            print(f"  [WARN] JSON parse failed, retrying LLM call... ({e})")
            retry_prompt = (
                prompt + "\n\n"
                "【重要提醒】你上次的输出不是合法的 JSON，导致解析失败。"
                "这次请务必只输出纯 JSON，不要有任何多余文字、注释或 markdown 标记。"
            )
            for attempt in range(max_retries):
                response = llm.invoke(retry_prompt)
                try:
                    return parse_json_response(response.content)
                except json.JSONDecodeError:
                    print(f"  [WARN] Retry {attempt + 1} also failed.")
                    continue
        raise
