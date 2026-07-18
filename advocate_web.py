import requests
import io
from pypdf import PdfReader
from bs4 import BeautifulSoup

from config import AD_EMAIL, AD_PASSWORD


BASE_URL = "https://advocatediaries.com"


class AdvocateWeb:

    def __init__(self, email=None, password=None):
        self.session = requests.Session()
        self.email = email or AD_EMAIL
        self.password = password or AD_PASSWORD
        self.logged_in = False


    def login(self):

        response = self.session.get(
            f"{BASE_URL}/auth/login"
        )

        soup = BeautifulSoup(
            response.text,
            "lxml"
        )

        csrf_input = soup.find(
            "input",
            {"name": "_csrfToken"}
        )

        if csrf_input is None:
            raise Exception(
                "Could not find CSRF token on login page"
            )

        csrf = csrf_input["value"]

        payload = {
            "_csrfToken": csrf,
            "email": self.email,
            "password": self.password,
            "submit": "Login"
        }

        headers = {
            "Referer": f"{BASE_URL}/auth/login",
            "Origin": BASE_URL
        }

        response = self.session.post(
            f"{BASE_URL}/auth/login",
            data=payload,
            headers=headers,
            allow_redirects=True
        )

        if "/auth/login" in response.url:
            raise Exception(
                "Advocate Diaries login failed"
            )

        self.logged_in = True

        return response


    def ensure_login(self):

        if not self.logged_in:
            self.login()


    def get(self, path, params=None):

        self.ensure_login()

        response = self.session.get(
            f"{BASE_URL}{path}",
            params=params,
            allow_redirects=True
        )

        return response


    def test_login(self):

        try:
            response = self.login()

            return True, response.url

        except Exception as e:

            return False, str(e)


    def attendance(self, date):

        self.ensure_login()

        response = self.session.get(
            f"{BASE_URL}/attendance/search-day-attendance",
            params={
                "attendance_date": date
            },
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{BASE_URL}/attendance"
            },
            allow_redirects=True
        )

        return response

    def works(self, status="pending"):
        return self.get(
            "/works",
            params={
                "status": status
            }
        )

    
    def complete_work(self, work_id):
        return self.get(
            "/works/mark_as_complete",
            params={"work": work_id}
        )
    
    def approve_attendance(self, attendance_id):
        self.ensure_login()

        return self.session.get(
            f"{BASE_URL}/attendance/approve-attendance",
            params={"attendance_id": attendance_id},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{BASE_URL}/attendance"
            },
            allow_redirects=True
        )
    def punch_in(self):

        return self.get(
            "/attendance/punch-in"
        )


    def punch_out(self):

        return self.get(
            "/attendance/punch-out"
        )

    def add_court_case_test(self):
        self.ensure_login()

        page = self.session.get(f"{BASE_URL}/courtCases/add")
        soup = BeautifulSoup(page.text, "lxml")

        csrf_input = soup.find(
            "input",
            {"name": "_csrfToken"}
        )

        if csrf_input is None:
            raise Exception("Could not find CSRF token for add case")

        csrf = csrf_input["value"]

        payload = {
            "_csrfToken": csrf,
            "client_id": "9d2b34e2-4ddd-48a0-baac-ba967cf8e9e4",
            "advocate_for": "Petitioner",
            "verses_name": "BOT TEST OPPOSITE",
            "verses_others": "",
            "client_type_id": "16",
            "case_title_petitioner": "BOT TEST CLIENT",
            "case_title_respondent": "BOT TEST OPPOSITE",
            "case_number": "",
            "judge_id": "058bc666-fb85-49dc-80c0-5c96774ba80b",
            "filing_number": "",
            "case_type_id": "11",
            "opponent_advocate_name": "",
            "first_hearing_date": "2026-07-08",
            "first_hearing_date_purpose": "Appearance",
            "agreement_number": "",
            "receiver_order_status": "",
            "receiver_order_date": "",
            "fir_number": "",
            "fir_date": "",
            "police_station": ""
        }

        response = self.session.post(
            f"{BASE_URL}/courtCases/add",
            data=payload,
            headers={
                "Referer": f"{BASE_URL}/courtCases/add",
                "Origin": BASE_URL
            },
            allow_redirects=False
        )

        return response

    def add_court_case(
        self,
        client_id,
        client_name,
        opposite_party,
        case_title_petitioner,
        case_title_respondent,
        client_type_id,
        case_type_id,
        judge_id,
        hearing_date,
        purpose="Appearance",
        advocate_for="Petitioner",
        case_number="",
        filing_number=""
    ):
        self.ensure_login()

        page = self.session.get(
            f"{BASE_URL}/courtCases/add"
        )

        soup = BeautifulSoup(
            page.text,
            "lxml"
        )

        csrf_input = soup.find(
            "input",
            {"name": "_csrfToken"}
        )

        if csrf_input is None:
            raise Exception(
                "Could not find CSRF token for add case"
            )

        csrf = csrf_input["value"]

        payload = {
            "_csrfToken": csrf,
            "client_id": client_id,
            "advocate_for": advocate_for,
            "verses_name": opposite_party,
            "verses_others": "",
            "client_type_id": str(client_type_id),
            "case_title_petitioner": case_title_petitioner,
            "case_title_respondent": case_title_respondent,
            "case_number": case_number,
            "judge_id": judge_id,
            "filing_number": filing_number,
            "case_type_id": str(case_type_id),
            "opponent_advocate_name": "",
            "first_hearing_date": hearing_date,
            "first_hearing_date_purpose": purpose,
            "agreement_number": "",
            "receiver_order_status": "",
            "receiver_order_date": "",
            "fir_number": "",
            "fir_date": "",
            "police_station": ""
        }

        response = self.session.post(
            f"{BASE_URL}/courtCases/add",
            data=payload,
            headers={
                "Referer": f"{BASE_URL}/courtCases/add",
                "Origin": BASE_URL
            },
            allow_redirects=False
        )

        return response

    def search_client(self, search_text, client_type="public"):
        self.ensure_login()

        response = self.session.get(
            f"{BASE_URL}/court-cases/search-client",
            params={
                "search": search_text,
                "type": client_type
            },
            headers={
                "X-Requested-With": "XMLHttpRequest"
            }
        )

        return response.json().get("clients", [])
    
    def search_client_type(self, search_text, client_type="public"):
        self.ensure_login()

        response = self.session.get(
            f"{BASE_URL}/court-cases/search-client-type",
            params={
                "search": search_text,
                "type": client_type
            },
            headers={
                "X-Requested-With": "XMLHttpRequest"
            }
        )

        return response.json().get("clientTypes", [])

    def search_judge(self, search_text, client_type="public"):
        self.ensure_login()

        response = self.session.get(
            f"{BASE_URL}/court-cases/search-judge",
            params={
                "search": search_text,
                "type": client_type
            },
            headers={
                "X-Requested-With": "XMLHttpRequest"
            }
        )

        return response.json().get("judges", [])


    def search_case_type(self, search_text, client_type="public"):
        self.ensure_login()

        response = self.session.get(
            f"{BASE_URL}/court-cases/search-case-type",
            params={
                "search": search_text,
                "type": client_type
            },
            headers={
                "X-Requested-With": "XMLHttpRequest"
            }
        )

        response.raise_for_status()

        return response.json().get("caseTypes", [])

    def download_day_cases_pdf(self, date):
        """
        Download the authenticated Advocate Diaries day-cases PDF.
        Date format: YYYY-MM-DD.
        """
        self.ensure_login()

        response = self.session.get(
            f"{BASE_URL}/dashboard/download-day-cases-pdf",
            params={"date": date},
            headers={
                "Referer": f"{BASE_URL}/dashboard"
            },
            allow_redirects=True,
            timeout=90
        )

        response.raise_for_status()

        content_type = (
            response.headers.get(
                "Content-Type",
                ""
            ).lower()
        )

        if (
            "pdf" not in content_type
            and not response.content.startswith(b"%PDF")
        ):
            raise Exception(
                "Advocate Diaries did not return a PDF."
            )

        return response.content

    def extract_day_cases_pdf_text(self, date):
        """
        Download and extract text from the Advocate Diaries day-cases PDF.
        """
        pdf_bytes = self.download_day_cases_pdf(date)
        reader = PdfReader(
            io.BytesIO(pdf_bytes)
        )

        return "\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )
