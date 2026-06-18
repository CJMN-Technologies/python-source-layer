# Facebook Event Scraper

This folder scrapes selected public Facebook pages for academic and LGU disruption signals near LRT-2 stations. It classifies relevant posts and saves them to Supabase.

## Purpose

The scraper looks for posts related to class suspensions, LGU advisories, weather disruptions, road closures, and similar external events that may affect LRT-2 demand or operations.

Target table:

```text
external.academic_lgu_events
```

## Tech Stack

| Technology | Purpose |
| --- | --- |
| Python | Main pipeline language |
| Playwright | Opens Facebook pages in headless Chromium |
| BeautifulSoup | Parses rendered HTML |
| Requests | Downloads image assets for OCR |
| Tesseract OCR | Extracts text from post images |
| pytesseract | Python bridge to Tesseract |
| Pillow | Loads images before OCR |
| Supabase Python client | Writes classified events to Supabase |
| python-dotenv | Loads local `.env` values |
| APScheduler | Optional local long-running scheduler |

## Files

| File | Purpose |
| --- | --- |
| `pipeline.py` | Main scraper pipeline and Supabase writer |
| `fb_scraper.py` | Playwright scraping, caption expansion, post age parsing, OCR extraction |
| `auth.py` | Builds Facebook cookies from environment variables |
| `keywords.py` | Classifies post text as `academic`, `lgu`, or irrelevant |
| `pages.json` | List of Facebook pages, stations, and scrape priorities |
| `scheduler.py` | Local scheduler for high, medium, and low priority pages |
| `Dockerfile` | Container image based on Playwright Python |
| `requirements.txt` | Python dependencies |

## Environment Variables

Create a local `.env` in this folder when running locally:

```env
SUPABASE_URL=
SUPABASE_KEY=
FB_C_USER=
FB_XS=
FB_DATR=
FB_FR=
FB_SB=
```

`FB_C_USER` and `FB_XS` are the most important cookies. The other Facebook cookies are supported when available and can improve session reliability.

Never commit `.env` or cookie values.

## Install

```bash
pip install -r requirements.txt
playwright install chromium
```

Tesseract must also be installed on the machine.

Windows default path used by the code:

```text
C:\Program Files\Tesseract-OCR\tesseract.exe
```

Linux default path used by the code:

```text
/usr/bin/tesseract
```

## Run Manually

From this folder:

```bash
python pipeline.py
```

Optional max post age in days:

```bash
python pipeline.py 3
```

The default max age is `5` days.

## Scheduling

Local scheduler:

```bash
python scheduler.py
```

Schedule behavior:

| Priority | Schedule |
| --- | --- |
| High | 5:00 AM and 3:00 PM daily |
| Medium | 12:00 AM daily |
| Low | Monday, Wednesday, Friday at 9:00 AM |

GitHub Actions uses the root workflow:

```text
.github/workflows/scraper.yml
```

## How Data Is Saved

For each relevant post, the pipeline saves:

- generated event ID such as `EVNTS-ACAD-0001` or `EVNTS-LGU-0001`
- station
- source page name
- source URL
- post text
- OCR image text when available
- category
- scrape timestamp

## Maintenance Notes

- Update `pages.json` when adding or removing source pages.
- Update `keywords.py` when classification rules change.
- Facebook markup can change, so scraper selectors may need maintenance.
- OCR quality depends on image clarity and installed Tesseract language data.
