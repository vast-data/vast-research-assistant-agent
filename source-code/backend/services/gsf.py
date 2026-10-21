"""
GSF (Generative SQL Foundation) integration service.

Provides a tool that calls the GSF API to run natural-language questions
against a structured database (e.g. healthineers-tinyehr) and returns
SQL + results via Server-Sent Events.
"""

import logging
import json
import os
import time
from typing import Optional, Dict, Any, List, Callable

import httpx

from config import (
    GSF_BASE_URL,
    GSF_API_KEY,
    GSF_TARGET_DB,
)

logger = logging.getLogger(__name__)

# Total wall-clock budget for one GSF question. GSF can spin for many minutes in a
# SQL repair loop, and its keepalive pings defeat the httpx read timeout.
GSF_MAX_SECONDS = float(os.getenv("GSF_MAX_SECONDS", "150"))
GSF_READ_TIMEOUT = 120.0


class GsfResponse:
    """Parsed response from a GSF streaming session."""

    def __init__(self):
        self.sql_code: Optional[str] = None
        self.results: Optional[List[Dict[str, Any]]] = None
        self.columns: Optional[List[str]] = None
        self.error: Optional[str] = None
        self.conversation_id: Optional[str] = None
        self.raw_events: List[Dict[str, Any]] = []
        self.duration_ms: float = 0
        self.steps: List[Dict[str, str]] = []


def query_gsf(
    question: str,
    conversation_id: Optional[str] = None,
    target_db: Optional[str] = None,
    on_step: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """
    Send a natural-language question to the GSF API and return structured results.

    This is the function registered as an agent tool. It streams SSE from GSF,
    collects SQL and result events, and returns a dict suitable for the agent.

    Args:
        question: Natural language question about the data
        conversation_id: Optional conversation ID (UUID) for multi-turn queries
        target_db: Database target (defaults to GSF_TARGET_DB config)
        on_step: Called with each pipeline step as it arrives, so the caller can
            forward progress to the browser while the query is still running.

    Returns:
        Dict with keys: sql_code, results, columns, error, conversation_id, steps
    """
    if not GSF_BASE_URL:
        return {"error": "NVIDIA Auto-Ontology is not configured (missing GSF_BASE_URL)"}

    db = target_db or GSF_TARGET_DB
    url = f"{GSF_BASE_URL.rstrip('/')}/api/chat/completions"

    payload: Dict[str, Any] = {
        "question": question,
        "target_db": db,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    headers: Dict[str, str] = {
        "Content-Type": "application/json",
    }
    if GSF_API_KEY:
        headers["x-api-key"] = GSF_API_KEY

    start_time = time.time()
    deadline = start_time + GSF_MAX_SECONDS
    response = GsfResponse()
    timed_out = False

    try:
        with httpx.Client(timeout=GSF_READ_TIMEOUT) as client:
            with client.stream("POST", url, json=payload, headers=headers) as stream:
                stream.raise_for_status()
                buffer = ""

                for chunk in stream.iter_text():
                    # GSF sends ": ping" keepalives, so the httpx read timeout never
                    # fires on a stuck query. Enforce our own wall-clock budget.
                    if time.time() > deadline:
                        timed_out = True
                        logger.warning(
                            f"GSF exceeded {GSF_MAX_SECONDS}s budget, abandoning query: {question[:120]}"
                        )
                        break

                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()

                        if not line or line == ": ping":
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                event = json.loads(data_str)
                                response.raw_events.append(event)
                                step_count = len(response.steps)
                                _process_gsf_event(event, response)
                                if on_step and len(response.steps) > step_count:
                                    for step in response.steps[step_count:]:
                                        try:
                                            on_step(step)
                                        except Exception as cb_err:
                                            logger.warning(f"GSF step callback failed: {cb_err}")
                            except json.JSONDecodeError:
                                logger.debug(f"GSF non-JSON line: {data_str[:100]}")

        response.duration_ms = (time.time() - start_time) * 1000

        result: Dict[str, Any] = {
            "sql_code": response.sql_code,
            "columns": response.columns,
            "results": response.results,
            "conversation_id": response.conversation_id,
            "duration_ms": response.duration_ms,
            "steps": response.steps,
        }

        if not response.error:
            if timed_out:
                response.error = (
                    f"The query took longer than {GSF_MAX_SECONDS}s and was stopped before it produced SQL."
                )
            elif response.results is None and not response.sql_code:
                # GSF ends a failed repair loop with a terminal step rather than an
                # error event, so an empty run would otherwise look like a success.
                last_step = response.steps[-1]["label"] if response.steps else ""
                response.error = (
                    f"The query could not be answered ({last_step})." if last_step
                    else "The query could not be answered."
                )
        if response.error:
            result["error"] = response.error

        logger.info(f"GSF query completed in {response.duration_ms:.0f}ms: "
                    f"sql={'yes' if response.sql_code else 'no'}, "
                    f"rows={len(response.results) if response.results else 0}"
                    f"{', error=' + response.error if response.error else ''}")

        return result

    except httpx.HTTPStatusError as e:
        duration_ms = (time.time() - start_time) * 1000
        # Streaming responses must be read before .text is available.
        body = ""
        try:
            body = e.response.read().decode("utf-8", errors="replace")
        except Exception:
            try:
                body = e.response.text
            except Exception:
                body = ""
        snippet = (body or "")[:500]
        logger.error(
            f"GSF API error: {e.response.status_code} - {snippet} [{duration_ms:.0f}ms]"
        )
        return {
            "error": f"NVIDIA Auto-Ontology API returned {e.response.status_code}: {snippet or e.response.reason_phrase}"
        }
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.error(f"GSF request failed: {e} [{duration_ms:.0f}ms]")
        return {"error": f"NVIDIA Auto-Ontology request failed: {str(e)}"}


def _process_gsf_event(event: Dict[str, Any], response: GsfResponse) -> None:
    """Process a single SSE event from GSF and update the response object."""
    event_type = event.get("type", "")

    if event_type == "step":
        step_info = {
            "node": event.get("node", ""),
            "label": event.get("label", ""),
        }
        if event.get("thought"):
            step_info["thought"] = event["thought"]
        response.steps.append(step_info)

    elif event_type == "result":
        answer = event.get("answer", {})
        if isinstance(answer, dict):
            response.sql_code = answer.get("sql_code")

            sql_response = answer.get("sql_response_from_db", [])
            if sql_response and isinstance(sql_response, list):
                raw = sql_response[0] if len(sql_response) == 1 else sql_response
                if isinstance(raw, str):
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, list):
                            response.results = parsed
                            if parsed and isinstance(parsed[0], dict):
                                response.columns = list(parsed[0].keys())
                    except json.JSONDecodeError:
                        pass
                elif isinstance(raw, list):
                    response.results = raw
                    if raw and isinstance(raw[0], dict):
                        response.columns = list(raw[0].keys())

    elif event_type == "sql":
        response.sql_code = event.get("sql", event.get("content", ""))

    elif event_type == "results":
        results_data = event.get("data", event.get("results", []))
        if isinstance(results_data, list) and results_data:
            response.results = results_data
            if isinstance(results_data[0], dict):
                response.columns = list(results_data[0].keys())

    elif event_type == "error":
        response.error = event.get("message", event.get("error", "Unknown error"))

    elif event_type == "conversation_id":
        response.conversation_id = event.get("id", event.get("conversation_id", ""))

    if "conversation_id" in event and not response.conversation_id:
        response.conversation_id = event["conversation_id"]

