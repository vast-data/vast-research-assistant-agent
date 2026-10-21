"""
CrowdStrike AIDR (AI Detection and Response) integration service.

Validates inputs/outputs against CrowdStrike's guard_chat_completions API
to detect malicious prompts, PII, secrets, and other threats.
"""

import logging
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

import httpx

from config import (
    CS_AIDR_BASE_URL,
    CS_AIDR_TOKEN,
    CS_AIDR_APP_ID,
    CS_AIDR_ENABLED,
)

logger = logging.getLogger(__name__)


@dataclass
class AidrDetectorResult:
    name: str
    detected: bool
    data: Optional[Dict[str, Any]] = None


@dataclass
class AidrGuardResult:
    blocked: bool
    transformed: bool
    policy: str
    detectors: List[AidrDetectorResult]
    request_id: str
    duration_ms: float
    raw_response: Dict[str, Any]

    @property
    def any_detected(self) -> bool:
        return any(d.detected for d in self.detectors)

    @property
    def detection_summary(self) -> str:
        detected = [d.name for d in self.detectors if d.detected]
        if not detected:
            return "No threats detected"
        return f"Detected: {', '.join(detected)}"

    @property
    def block_reason(self) -> Optional[str]:
        if not self.blocked:
            return None
        detected = [d for d in self.detectors if d.detected]
        reasons = []
        for d in detected:
            if d.data and "analyzer_responses" in d.data:
                for analyzer in d.data["analyzer_responses"]:
                    reasons.append(f"{analyzer.get('analyzer', d.name)} (confidence: {analyzer.get('confidence', 'N/A')})")
            else:
                reasons.append(d.name)
        return "; ".join(reasons) if reasons else "Policy violation"


def guard_chat_input(
    messages: List[Dict[str, str]],
    user_id: str = "anonymous",
    model: str = "gpt-4o",
    llm_provider: str = "openai",
) -> Optional[AidrGuardResult]:
    """
    Validate user input messages against CrowdStrike AIDR.

    Args:
        messages: List of chat messages (role + content)
        user_id: User identifier for audit trail
        model: LLM model name
        llm_provider: LLM provider name

    Returns:
        AidrGuardResult or None if AIDR is disabled/unavailable
    """
    return _call_guard(
        messages=messages,
        event_type="input",
        user_id=user_id,
        model=model,
        llm_provider=llm_provider,
    )


def guard_chat_output(
    messages: List[Dict[str, str]],
    user_id: str = "anonymous",
    model: str = "gpt-4o",
    llm_provider: str = "openai",
) -> Optional[AidrGuardResult]:
    """
    Validate agent output against CrowdStrike AIDR.

    Args:
        messages: List of chat messages including the assistant response
        user_id: User identifier for audit trail
        model: LLM model name
        llm_provider: LLM provider name

    Returns:
        AidrGuardResult or None if AIDR is disabled/unavailable
    """
    return _call_guard(
        messages=messages,
        event_type="output",
        user_id=user_id,
        model=model,
        llm_provider=llm_provider,
    )


def guard_tool_output(
    tool_name: str,
    tool_output: str,
    user_id: str = "anonymous",
    model: str = "gpt-4o",
    llm_provider: str = "openai",
) -> Optional[AidrGuardResult]:
    """
    Validate tool output (e.g., RAG chunks, search results) against AIDR.

    Wraps the tool output as a user message to check for indirect injection.

    Args:
        tool_name: Name of the tool that produced the output
        tool_output: The text content returned by the tool
        user_id: User identifier for audit trail
        model: LLM model name
        llm_provider: LLM provider name

    Returns:
        AidrGuardResult or None if AIDR is disabled/unavailable
    """
    messages = [
        {"role": "user", "content": tool_output}
    ]
    return _call_guard(
        messages=messages,
        event_type="input",
        user_id=user_id,
        model=model,
        llm_provider=llm_provider,
    )


def _call_guard(
    messages: List[Dict[str, str]],
    event_type: str,
    user_id: str,
    model: str,
    llm_provider: str,
) -> Optional[AidrGuardResult]:
    """Internal: call the CrowdStrike AIDR guard_chat_completions endpoint."""
    if not CS_AIDR_ENABLED:
        logger.debug("AIDR is disabled, skipping guard check")
        return None

    if not CS_AIDR_BASE_URL or not CS_AIDR_TOKEN:
        logger.warning("AIDR credentials not configured, skipping guard check")
        return None

    url = f"{CS_AIDR_BASE_URL.rstrip('/')}/v1/guard_chat_completions"

    payload = {
        "guard_input": {
            "messages": messages
        },
        "event_type": event_type,
        "app_id": CS_AIDR_APP_ID,
        "user_id": user_id,
        "llm_provider": llm_provider,
        "model": model,
    }

    start_time = time.time()
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {CS_AIDR_TOKEN}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        duration_ms = (time.time() - start_time) * 1000
        result_data = data.get("result", {})

        detectors = []
        for name, info in result_data.get("detectors", {}).items():
            detectors.append(AidrDetectorResult(
                name=name,
                detected=info.get("detected", False),
                data=info.get("data"),
            ))

        guard_result = AidrGuardResult(
            blocked=result_data.get("blocked", False),
            transformed=result_data.get("transformed", False),
            policy=result_data.get("policy", "unknown"),
            detectors=detectors,
            request_id=data.get("request_id", ""),
            duration_ms=duration_ms,
            raw_response=data,
        )

        if guard_result.blocked:
            logger.warning(
                f"AIDR BLOCKED ({event_type}): {guard_result.block_reason} "
                f"[request_id={guard_result.request_id}, user={user_id}]"
            )
        elif guard_result.any_detected:
            logger.info(
                f"AIDR DETECTED ({event_type}): {guard_result.detection_summary} "
                f"[request_id={guard_result.request_id}, user={user_id}]"
            )
        else:
            logger.debug(f"AIDR OK ({event_type}) [{duration_ms:.0f}ms]")

        return guard_result

    except httpx.HTTPStatusError as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.error(f"AIDR API error: {e.response.status_code} - {e.response.text} [{duration_ms:.0f}ms]")
        return None
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.error(f"AIDR request failed: {e} [{duration_ms:.0f}ms]")
        return None
