"""LLM initialization."""

import os

from langchain_openai import ChatOpenAI


def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.7,
        base_url=os.getenv("OPENAI_API_BASE", None),
    )
