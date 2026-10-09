# Sprint 45 — Next-day eCourts date checks

Extract over the current project, retaining folders, and redeploy Railway. Includes the current Sprint 44 bot base; deploy Sprint 44 first if not already installed.

Automatic Google Drive backup reconciliation now runs once daily at 10:05 AM IST. The old six-hour interval and restart-triggered run are removed. ECOURTS_BACKUP_SYNC_HOURS no longer controls this job. Manual /syncecourts remains available.

Different dates are deferred when staff last-hearing date is today, or when eCourts still lists today/an earlier date while staff has a future date. Matching dates remain verified. On the next day, fresh differing future dates remain reviewable. Date comparisons use Asia/Kolkata rather than container UTC. Existing stale conflict cards cannot apply their eCourts date through Accept; the button rejects the request. Existing messages are not deleted.

The bot reads the backups available in Google Drive. It does not force eCourts or the backup exporter to publish fresh data by 10:05. Stale backups remain awaiting publication. Generic non-date change alerts and other jobs retain their current schedules.

After deploying run /syncecourts once to recalculate existing stored date verifications, then inspect /ecourtsdates. Check that cases with eCourts next date today and future staff dates no longer appear as pending date conflicts. No staff date is overwritten by the sync.

Verification: 10 date-rule tests passed including today-date delay, same-day last-hearing delay, next-day genuine difference, and next-day still-stale data. All changed Python files compile. Live sync was not run locally.
