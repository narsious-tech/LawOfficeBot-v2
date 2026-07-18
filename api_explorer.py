import requests
from advocate_diaries import AdvocateDiaries

BASE_URL = "https://advocatediaries.com/api/v1"

ENDPOINTS = {
    "attendance": [
        ("/attendance", "GET"),
        ("/attendance/list", "GET"),
        ("/attendance/history", "GET"),
        ("/attendance/checkin", "POST"),
        ("/attendance/checkout", "POST"),
        ("/attendance/search-day-attendance", "GET"),
        ("/attendance/report", "GET"),
        ("/attendance/search", "GET"),
        ("/attendance/all", "GET"),
    ],

    "leave": [
        ("/leave", "GET"),
        ("/leaves", "GET"),
        ("/leave/history", "GET"),
    ],

    "tasks": [
        ("/tasks", "GET"),
        ("/tasks/today", "GET"),
    ],

    "calendar": [
        ("/calendar", "GET"),
    ],

    "clients": [
        ("/clients", "GET"),
    ],
}


def run_api_explorer(module):

    ad = AdvocateDiaries()
    ad.login()

    headers = ad.headers()

    if module not in ENDPOINTS:
        return "Unknown module."

    output = []

    for endpoint, method in ENDPOINTS[module]:

        url = BASE_URL + endpoint

        try:

            if method == "GET":
                r = requests.get(url, headers=headers)
            else:
                r = requests.post(url, headers=headers)

            output.append(
                f"{method:5} {endpoint:35} {r.status_code}"
            )

        except Exception as e:

            output.append(f"{endpoint} -> {e}")

    return "\n".join(output)
