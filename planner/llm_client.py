# -*- coding: utf-8 -*-
"""統一封裝所有語言模型呼叫（階段 3–4 共用）。

設計原則（CLAUDE.md）：
- 環境變數 LLM_PROVIDER 決定供應商，預設 azure_openai；另提供 mock。
- mock：不呼叫任何 API，直接回傳呼叫端提供的確定性 fallback，讓沒有金鑰的
  組員也能跑完整流程。
- 真實呼叫：要求只輸出 JSON，移除 ```json 標記後解析；逾時 15 秒；
  任何失敗（逾時、網路、JSON 解析失敗）都回傳 fallback，不讓流程中斷。
- 金鑰只存在後端環境變數，不寫入程式碼、不回傳前端。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

DEFAULT_TIMEOUT = 15.0
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def strip_json_fences(text: str) -> str:
    """移除 ```json ... ``` 標記，回傳中間的純 JSON 文字。"""
    text = text.strip()
    text = _FENCE_RE.sub("", text)
    # 若仍夾雜前後文字，擷取第一個 { 到最後一個 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text


def parse_json_object(text: str) -> dict[str, Any]:
    """解析 JSON 物件；失敗則丟出 ValueError。"""
    obj = json.loads(strip_json_fences(text))
    if not isinstance(obj, dict):
        raise ValueError("回應不是 JSON 物件")
    return obj


class LLMClient:
    """語言模型客戶端。對外只有 complete_json 一個方法。"""

    def __init__(self, provider: str | None = None, timeout: float = DEFAULT_TIMEOUT):
        self.provider = (provider or os.environ.get("LLM_PROVIDER", "azure_openai")).lower()
        self.timeout = timeout
        self._azure = None  # 延遲初始化

    # ---------------------------------------------------------------- public
    def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        fallback: dict[str, Any] | None = None,
        max_tokens: int = 800,
    ) -> dict[str, Any]:
        """送出對話，取得 JSON 物件。

        mock 模式或任何失敗，皆回傳 fallback（None 時回傳 {}）。
        呼叫端應提供一個「確定性、非 AI」的 fallback，作為 mock 結果與錯誤退路。
        """
        fb = {} if fallback is None else fallback
        if self.provider == "mock":
            return fb
        try:
            text = self._call(messages, max_tokens)
            return parse_json_object(text)
        except Exception as e:  # noqa: BLE001  逾時/網路/解析失敗 → fallback
            print(f"[llm_client] 呼叫或解析失敗（{type(e).__name__}: {e}），改用 fallback。")
            return fb

    # ---------------------------------------------------------------- private
    def _call(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        if self.provider == "azure_openai":
            return self._call_azure(messages, max_tokens)
        raise ValueError(f"未知的 LLM_PROVIDER：{self.provider}")

    def _call_azure(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        if self._azure is None:
            from openai import AzureOpenAI  # 延遲 import，未安裝也不影響 mock
            endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
            api_key = os.environ["AZURE_OPENAI_API_KEY"]
            api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
            self._azure = AzureOpenAI(
                api_key=api_key, azure_endpoint=endpoint, api_version=api_version,
            )
            self._deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
        resp = self._azure.chat.completions.create(
            model=self._deployment,
            messages=messages,
            temperature=0,
            max_tokens=max_tokens,
            timeout=self.timeout,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""


def get_client(provider: str | None = None) -> LLMClient:
    """取得預設客戶端（依環境變數）。"""
    return LLMClient(provider=provider)
