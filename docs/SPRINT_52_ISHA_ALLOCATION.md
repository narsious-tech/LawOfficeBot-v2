# Manual daily hearing allocation to Isha

1. Upload commands/associate_allocation.py into the commands folder.
2. In bot.py, immediately before app.add_handler(build_judge_leave_handler()), add:

    from commands.associate_allocation import register_associate_allocation
    register_associate_allocation(app)

3. Commit and wait for Railway Active. No new variables needed. The allocation audit table is created on first confirmation.
4. Ajay or Priya opens the private Telegram bot and sends /assignisha or /assignisha DD-MM-YYYY. Dates: today through 14 days ahead.
5. Tap individual cases, use Previous/Next, Review selected, then Confirm and send to Isha. Cancel does not send a new list.

Selection takes place in Telegram. Delivery goes to Isha's exact active staff profile on Telegram and WhatsApp. The existing free-window WhatsApp queue is used; when the 24-hour window is closed Isha must message the bot. The queue expires at midnight after the hearing date. Delivery results distinguish sent, queued and unavailable. No paid template fallback.

Each confirmation is an additional batch, not a replacement of prior batches. Check previous deliveries before creating another batch for the same date. Starting /assignisha again replaces your unfinished selection. Unconfirmed selections expire when the bot restarts; confirmed batches are recorded in associate_daily_allocations. This version sends allocation lists; it does not add attendance/completion status buttons or change permanent case ownership.

Confirmed judge-leave cases are excluded. Confirmation reloads the cause list; if a selected case is removed or its identity changes, start a fresh selection. Case title, court, judge, purpose, floor and room are taken from Advocate Diaries when available.

Validation: Python compilation plus five pure selection/render tests. Live database and message delivery must be verified after deployment.
