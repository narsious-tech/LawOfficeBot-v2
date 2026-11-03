# Sprint 51 — Heavy Hearing Dates

The bot now identifies future hearing dates that may overload preparation work.

## Access

- Telegram: `/heavydates` or `/heavydates 60`
- Owner WhatsApp: `HEAVY DATES`
- The morning Command Centre shows the next three heavy dates automatically.

## Default rules

A date is shown when either condition is met:

- 8 or more active matters are listed on that date; or
- 3 or more active matters have the same hearing purpose on that date.

The report shows the total load, repeated purposes, and affected case numbers.
Closed and disposed cases are ignored. No external API call or paid WhatsApp template is used.

## Optional Railway variables

```text
HEAVY_DATE_LOOKAHEAD_DAYS=45
HEAVY_DATE_MIN_CASES=8
HEAVY_PURPOSE_MIN_CASES=3
VERY_HEAVY_DATE_MIN_CASES=12
VERY_HEAVY_PURPOSE_MIN_CASES=6
```

The defaults work without adding these variables.
