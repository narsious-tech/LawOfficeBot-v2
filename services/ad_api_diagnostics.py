import json
from typing import Any, Dict, List, Optional

import requests

from services.ad_sync_v2 import (
    AD_API,
    login,
)


def api_headers() -> Dict[str, str]:
    access_token, _ = login()

    return {
        "Authorization": (
            f"Bearer {access_token}"
        ),
        "Accept": "application/json",
    }


def fetch_case_by_search(
    case_value: str
) -> Dict[str, Any]:
    """
    Search Advocate Diaries court cases and return
    the first matching raw case payload.
    """
    headers = api_headers()

    queries = [
        {
            "search": case_value
        },
        {
            "case_number": case_value
        },
    ]

    last_response = None

    for params in queries:
        response = requests.get(
            f"{AD_API}/court_cases",
            params=params,
            headers=headers,
            timeout=(10, 60)
        )

        last_response = response

        if response.status_code >= 400:
            continue

        body = response.json()
        data = body.get("data") or []

        if isinstance(data, dict):
            data = (
                data.get("items")
                or data.get("cases")
                or data.get("data")
                or []
            )

        if not isinstance(data, list):
            continue

        normalized_target = (
            str(case_value)
            .strip()
            .lower()
        )

        for item in data:
            if not isinstance(item, dict):
                continue

            candidate_values = [
                item.get("case_number"),
                item.get("case_no"),
                item.get("registration_number"),
                item.get("case_id"),
                item.get("id"),
            ]

            normalized_candidates = {
                str(value).strip().lower()
                for value in candidate_values
                if value is not None
            }

            if normalized_target in normalized_candidates:
                return {
                    "success": True,
                    "endpoint": (
                        f"{AD_API}/court_cases"
                    ),
                    "params": params,
                    "payload": item,
                }

        if data:
            return {
                "success": True,
                "endpoint": (
                    f"{AD_API}/court_cases"
                ),
                "params": params,
                "payload": data[0],
                "warning": (
                    "No exact case-number match was found; "
                    "returning the first search result."
                ),
            }

    return {
        "success": False,
        "status_code": (
            last_response.status_code
            if last_response is not None
            else None
        ),
        "response_text": (
            last_response.text[:2000]
            if last_response is not None
            else "No response"
        ),
    }


def extract_possible_client_ids(
    payload: Any
) -> List[str]:
    """
    Recursively collect values from keys that may refer
    to a client identifier.
    """
    results = []

    candidate_keys = {
        "client_id",
        "ad_client_id",
        "client_uuid",
        "party_id",
        "litigant_id",
    }

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if (
                    key.lower()
                    in candidate_keys
                    and child is not None
                ):
                    results.append(
                        str(child)
                    )

                walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)

    return list(
        dict.fromkeys(results)
    )


def try_client_endpoints(
    client_id: str
) -> List[Dict[str, Any]]:
    """
    Probe likely official REST endpoints for a client ID.

    This is diagnostic only. It does not modify data.
    """
    headers = api_headers()

    endpoint_tests = [
        (
            f"{AD_API}/clients/{client_id}",
            None
        ),
        (
            f"{AD_API}/clients",
            {
                "id": client_id
            }
        ),
        (
            f"{AD_API}/clients",
            {
                "client_id": client_id
            }
        ),
        (
            f"{AD_API}/client/{client_id}",
            None
        ),
    ]

    results = []

    for url, params in endpoint_tests:
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=(10, 45)
            )

            content_type = (
                response.headers.get(
                    "content-type",
                    ""
                )
            )

            if "json" in content_type.lower():
                try:
                    body = response.json()
                except Exception:
                    body = response.text[:2000]
            else:
                body = response.text[:2000]

            results.append({
                "url": url,
                "params": params,
                "status_code": (
                    response.status_code
                ),
                "body": body,
            })

        except Exception as exc:
            results.append({
                "url": url,
                "params": params,
                "error": (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            })

    return results


def pretty_json(
    value: Any,
    limit: int = 12000
) -> str:
    text = json.dumps(
        value,
        indent=2,
        ensure_ascii=False,
        default=str
    )

    if len(text) > limit:
        return (
            text[:limit]
            + "\n... [truncated]"
        )

    return text
