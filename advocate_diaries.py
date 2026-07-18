import os
import requests

BASE_URL = os.getenv("AD_API")
EMAIL = os.getenv("AD_EMAIL")
PASSWORD = os.getenv("AD_PASSWORD")


class AdvocateDiaries:

    def __init__(self):
        self.access_token = None
        self.refresh_token = None

    def login(self):

        response = requests.post(
            f"{BASE_URL}/login",
            json={
                "email": EMAIL,
                "password": PASSWORD
            }
        )

        data = response.json()

        if not data["success"]:
            raise Exception(data["message"])

        self.access_token = data["data"]["access_token"]
        self.refresh_token = data["data"]["refresh_token"]

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
            headers=self.headers()
        )

        return response.json()

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
