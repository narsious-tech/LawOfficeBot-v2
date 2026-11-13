# iPhone Shortcut compatibility restore
Upload services/ios_dashboard_api.py, preserving the services folder. No attendance_app.py replacement is needed when Heavy Dates already works.

Restored GET routes: /api/ios/morning-dashboard, /api/ios/office-dashboard, /api/ios/office-status, /api/ios/who-in-office, /api/ios/deadlines, /api/ios/work-deadlines.
These return summary and display_text. Existing shortcuts can keep summary. Heavy Dates keeps display_text. All routes require the existing Bearer header and owner authorization.

Deadlines reports due-today, overdue and pending work counts. Attendance uses the existing active WhatsApp-linked staff attendance report. Morning uses the existing morning dashboard builder.

Keep IOS_DASHBOARD_TOKEN and ADMIN_USER_ID configured. IOS_OFFICE_DASHBOARD_TOKEN is also accepted as a fallback.
Validated with mocked service tests; not deployed or tested against the live database.
