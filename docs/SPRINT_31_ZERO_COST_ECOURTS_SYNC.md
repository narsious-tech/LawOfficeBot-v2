# Sprint 31 — Zero-cost next-day eCourts date sync

This deployment removes scheduled paid eCourtsIndia `CASE_DETAIL` polling.
Only the Google Drive district/high-court backup reconciliation remains, and it
runs once each morning at 07:30 Asia/Kolkata.  Accordingly, hearings from the
previous day are checked the following morning rather than repeatedly on the
same day.

Set these Railway variables before or immediately after deployment:

```
ECOURTSINDIA_API_ENABLED=false
ECOURTSINDIA_PAID_API_ALLOWED=false
ECOURTS_BACKUP_SYNC_HOUR_IST=7
ECOURTS_BACKUP_SYNC_MINUTE_IST=30
```

Also delete `ECOURTSINDIA_API_KEY` from Railway to eliminate accidental future
use.  The code includes a second cost-consent lock, so retaining an old key by
mistake cannot enable paid calls unless both API flags are explicitly true.

The following are no longer scheduled:

- Paid eCourtsIndia order/case-detail polling
- Automatic eCourts Order Inbox polling
- Same-day six-hour backup reconciliation
- The separate daily eCourts operations summary

Telegram, Office OS, Advocate Diaries, Google Drive backups, the administrator
review queue, and explicit approval before any Office OS date update remain.
