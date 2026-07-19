import os
import requests

BASE_URL = (os.getenv("AD_API") or "").rstrip("/")
EMAIL = os.getenv("AD_EMAIL")
PASSWORD = os.getenv("AD_PASSWORD")


class AdvocateDiaries:

    def __init__(self):
        self.access_token = None
        self.refresh_token = None

    def login(self):

        if not BASE_URL:
            raise RuntimeError("AD_API is not configured.")
        if not EMAIL or not PASSWORD:
            raise RuntimeError("AD_EMAIL or AD_PASSWORD is not configured.")

        response = requests.post(
            f"{BASE_URL}/login",
            json={
                "email": EMAIL,
                "password": PASSWORD
            },
            timeout=(10, 60)
        )
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Advocate Diaries login returned non-JSON content (HTTP {response.status_code})."
            ) from exc

        if not data.get("success"):
            raise RuntimeError(data.get("message") or "Advocate Diaries login failed.")

        token_data = data.get("data") or {}
        self.access_token = token_data.get("access_token")
        self.refresh_token = token_data.get("refresh_token")
        if not self.access_token:
            raise RuntimeError("Advocate Diaries login succeeded but no access token was returned.")

        return True

    def headers(self):

        if self.access_token is None:
            self.login()

        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json"
        }

    def daily_cause_list(self, date):

        response = requests.get(
            f"{BASE_URL}/court_cases/daily_cause_list",
            params={
                "date": date
            },
            headers=self.headers(),
            timeout=(10, 90)
        )
        response.raise_for_status()

        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Daily cause list returned non-JSON content (HTTP {response.status_code})."
            ) from exc

    def get_users(self):

        response = requests.get(
            f"{BASE_URL}/users",
            headers=self.headers()
        )

        return response.json()

    def search_users(self, search):

        response = requests.get(
            f"{BASE_URL}/users",
            params={
                "search": search
            },
            headers=self.headers()
        )

        return response.json()
    def test_attendance_api(self, date):

        response = requests.get(
        f"{BASE_URL}/attendance/search-day-attendance",
            params={
                "attendance_date": date
            },
            headers=self.headers()
        )

        return response.status_code, response.text
