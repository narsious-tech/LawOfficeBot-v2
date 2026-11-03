# Sprint 51 — Heavy Hearing Dates

The bot now identifies future hearing dates that may overload preparation work.

## Access

- Telegram: `/heavydates` or `/heavydates 60`
- Telegram detail: `/heavydate 04-11-2026`
- Owner WhatsApp: `HEAVY DATES`
- Owner WhatsApp detail: `HEAVY DATE 04-11-2026`
- The morning Command Centre shows the next three heavy dates automatically.

## Default rules

A date is shown when either condition is met:

- 8 or more active matters are listed on that date; or
- 3 or more active matters have the same hearing purpose on that date.

The main report is a compact date heatmap. The detail command shows repeated
purposes, case titles and case numbers. Duplicate Office OS case rows are counted
once, and missing purposes are shown as a data-quality warning rather than being
treated as a genuine shared purpose.

Case-number aliases with the same case title (for example `EA/59/2026` and
`DC/36/EA/59/2026`) are treated as one matter. Combined purposes are assigned
to each relevant preparation group, so `PWS + consd` contributes to both
Plaintiff evidence and Consideration.
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
