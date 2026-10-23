"""Owner-only Shortcuts API; no query-string credentials or user selection."""
import hmac
import os
import re
from datetime import datetime, timezone

from flask import jsonify, request


def register_ios_office_dashboard(app):
    def reply(payload, status=200):
        response = jsonify(payload)
        response.status_code = status
        response.headers['Cache-Control'] = 'no-store, private'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Vary'] = 'Authorization'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def authorize():
        token = os.getenv('IOS_OFFICE_DASHBOARD_TOKEN', '')
        admin = os.getenv('ADMIN_USER_ID', '').strip()
        if (os.getenv('IOS_OFFICE_DASHBOARD_ENABLED', '').lower() != 'true'
                or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', token)
                or not re.fullmatch(r'[1-9][0-9]{0,18}', admin)):
            return reply({'ok': False, 'error': 'Dashboard unavailable'}, 503)
        auth = request.headers.get('Authorization', '')
        expected = 'Bearer ' + token
        if not hmac.compare_digest(auth.encode('utf-8'), expected.encode('ascii')):
            response = reply({'ok': False, 'error': 'Unauthorized'}, 401)
            response.headers['WWW-Authenticate'] = 'Bearer'
            return response
        if request.method != 'GET':
            response = reply({'ok': False, 'error': 'Method not allowed'}, 405)
            response.headers['Allow'] = 'GET'
            return response
        if request.query_string:
            return reply({'ok': False, 'error': 'Query parameters are not supported'}, 400)
        return int(admin)

    def dashboard():
        admin = authorize()
        if not isinstance(admin, int):
            return admin
        try:
            from services.role_intelligence_service import role_dashboard
            data = role_dashboard(int(admin), read_only=True)
            if not data['profile'].get('is_active'):
                return reply({'ok': False, 'error': 'Access denied'}, 403)
            files = {k: int(data['files'].get(k) or 0) for k in
                     ('pending', 'brought', 'not_found', 'attention')}
            works = {k: int(data['works'].get(k) or 0) for k in
                     ('pending', 'due_today', 'overdue')}
            updates = int(data.get('pending_updates') or 0)
            summary = '\n'.join([
                'OFFICE DASHBOARD',
                f"Files selected/pending: {files['pending']}",
                f"Brought: {files['brought']} | Not found: {files['not_found']}",
                f"Needs attention: {files['attention']}",
                f"Works pending: {works['pending']}",
                f"Due today: {works['due_today']} | Overdue: {works['overdue']}",
                f'Pending hearing updates: {updates}',
            ])
            return reply({'ok': True, 'generated_at': datetime.now(timezone.utc).isoformat(),
                          'files': files, 'works': works, 'pending_updates': updates,
                          'summary': summary})
        except Exception:
            # Never return database errors, credentials, or case contents to clients.
            app.logger.error('iOS office dashboard retrieval failed')
            return reply({'ok': False, 'error': 'Dashboard unavailable'}, 503)

    app.add_url_rule('/api/ios/office-dashboard', 'ios_office_dashboard', dashboard,
                     methods=['GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE'],
                     provide_automatic_options=False)

    def report_view(kind):
        def view():
            admin = authorize()
            if not isinstance(admin, int):
                return admin
            stage = 'profile_read'
            try:
                from services.role_intelligence_service import staff_profile
                if not staff_profile(admin, read_only=True).get('is_active'):
                    return reply({'ok': False, 'error': 'Access denied'}, 403)
                stage = 'report_import'
                from services.ios_court_reports import build_court_report, build_morning_report
                stage = 'report_build'
                data = build_morning_report() if kind == 'morning' else build_court_report(kind)
                return reply(data)
            except Exception as exc:
                error_type = type(exc).__name__
                # PostgreSQL SQLSTATE is a five-character code, not error text.
                sqlstate = getattr(exc, 'pgcode', None)
                sqlstate = sqlstate if isinstance(sqlstate, str) and re.fullmatch(r'[0-9A-Z]{5}', sqlstate) else None
                app.logger.error('iOS court report retrieval failed: %s stage=%s type=%s sqlstate=%s',
                                 kind, stage, error_type, sqlstate or '-')
                code = stage + ':' + error_type + (':' + sqlstate if sqlstate else '')
                summary = 'Report unavailable. Diagnostic: ' + code
                return reply({'ok': False, 'error': 'Report unavailable',
                              'diagnostic': code, 'summary': summary}, 503)
        return view

    for path, kind in (('today-hearings', 'today'),
                       ('morning-dashboard', 'morning'),
                       ('evening-dashboard', 'evening')):
        app.add_url_rule('/api/ios/' + path, 'ios_report_' + kind, report_view(kind),
                         methods=['GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE'],
                         provide_automatic_options=False)

    def task_report_view(kind):
        def view():
            admin = authorize()
            if not isinstance(admin, int):
                return admin
            try:
                from services.role_intelligence_service import staff_profile
                if not staff_profile(admin, read_only=True).get('is_active'):
                    return reply({'ok': False, 'error': 'Access denied'}, 403)
                from services.ios_task_reports import build_task_report
                data = build_task_report(kind)
                return reply(data, 200 if data['ok'] else 503)
            except Exception as exc:
                app.logger.error('iOS task report failed: %s type=%s', kind, type(exc).__name__)
                return reply({'ok': False, 'error': 'Task report unavailable',
                              'summary': 'Task report unavailable. Check Railway logs.'}, 503)
        return view

    for path, kind in (('office-tasks', 'office_tasks'),
                       ('overdue-tasks', 'overdue'),
                       ('due-today-tasks', 'due_today'),
                       ('task-deadlines', 'deadlines')):
        app.add_url_rule('/api/ios/' + path, 'ios_tasks_' + kind, task_report_view(kind),
                         methods=['GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE'],
                         provide_automatic_options=False)

    def who_in_office():
        admin = authorize()
        if not isinstance(admin, int):
            return admin
        try:
            from services.role_intelligence_service import staff_profile
            if not staff_profile(admin, read_only=True).get('is_active'):
                return reply({'ok': False, 'error': 'Access denied'}, 403)
            from services.ios_attendance_reports import build_who_in_office_report
            return reply(build_who_in_office_report())
        except Exception as exc:
            app.logger.error('iOS attendance report failed: type=%s', type(exc).__name__)
            return reply({'ok': False, 'error': 'Attendance report unavailable',
                          'summary': 'Attendance report unavailable. Check Railway logs.'}, 503)

    app.add_url_rule('/api/ios/who-in-office', 'ios_who_in_office', who_in_office,
                     methods=['GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE'],
                     provide_automatic_options=False)
