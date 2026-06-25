# Academic Calendar Release Scraper Documentation

## 1. Context, Purpose, and Keywords

**Context**
Universities and colleges frequently use social media platforms like Facebook to announce their academic schedules. Despite different school branding, mottos, and specific hashtags (e.g., "Iskolar ng Bayan", "#ProDeoEtPatria"), these announcements share a universal blueprint. They consistently focus on the document name, the academic year, and availability indicators or links.

**Purpose**
The purpose of this document is to provide a generalized, source-agnostic keyword filtering strategy and logic ruleset for a Facebook scraper. This ensures the system accurately captures official Academic Year 2026-2027 calendar releases across multiple institutions while automatically filtering out drafts, tentative schedules, and irrelevant school-specific noise.

**Keywords**
To build a highly accurate filter, keywords are divided into functional categories:

*   **Primary Identifiers (The Target Document):** 
    `Academic Calendar`, `University Calendar`, `School Calendar`, `Collegiate Calendar`
*   **Timeframe Indicators (The Target Year):** 
    `A.Y. 2026-2027`, `AY 2026-2027`, `A.Y. 26-27`, `S.Y. 2026-2027`, `SY 2026-2027`
*   **Action Triggers (The Release Status):** 
    `released`, `out now`, `now available`, `view`, `download`, `access`
*   **Structural Signals (Link Formats):** 
    `bit.ly`, `tinyurl.com`, `cutt.ly`, `.edu`, `.edu.ph`
*   **Negative Keywords (Strict Exclusions):** 
    `draft`, `drafting`, `proposed`, `tentative`, `subject to change`

---

## 2. Boolean Filter Logic

To minimize false positives (spam or irrelevant posts) and maximize the catch rate, the scraper evaluates scraped text using the following Boolean logic constraint:

**Core Condition:**
> **IF** `[Primary Identifier]` 
> **AND** `[Timeframe Indicator]` 
> **AND** `([Action Trigger] OR [Structural Signal])`
> **AND NOT** `[Negative Keyword]`
> **THEN** -> Flag Post as a Valid Calendar Release.

---

## 3. JSON Configuration Payload (`config.json`)

You can save this generalized configuration block as a `.json` file to be ingested by your scraper application.

```json
{
  "academic_calendar_scraper": {
    "version": "1.1",
    "target_year": "2026-2027",
    "filters": {
      "primary_identifiers": [
        "Academic Calendar",
        "University Calendar",
        "School Calendar",
        "Collegiate Calendar"
      ],
      "timeframe_indicators": [
        "A.Y. 2026-2027",
        "AY 2026-2027",
        "A.Y. 26-27",
        "S.Y. 2026-2027",
        "SY 2026-2027"
      ],
      "action_triggers": [
        "released",
        "out now",
        "now available",
        "view",
        "download",
        "access"
      ],
      "structural_signals": [
        "bit.ly",
        "tinyurl.com",
        "cutt.ly",
        ".edu",
        ".edu.ph"
      ]
    },
    "negative_keywords": [
      "draft",
      "drafting",
      "proposed",
      "tentative",
      "subject to change"
    ]
  }
}