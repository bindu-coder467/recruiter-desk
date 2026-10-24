# C2C Recruiter Desk

A tool that finds recruiter contacts from hiring posts and sends templated outreach emails from your own Gmail account, with review steps, a daily cap, follow-ups and reply tracking.

## What you need
Python 3.8 or newer, a free Adzuna key, a Gmail App Password, and optionally a LinkedIn developer app for the login. There are no other installs. An Anthropic API key is optional and only used by "Extract with AI".

## How it meets the task
| Task item | How this project handles it |
|---|---|
| LinkedIn login | Official "Sign in with LinkedIn" (OAuth, OpenID Connect) in app.py. Automated password login and session scraping are not used because LinkedIn's terms do not allow them. |
| Search posts with the keywords | "Open LinkedIn search" opens LinkedIn's own post search (Latest, Past 24 hours) with the role, C2C and the excluded words (-bench -sales -hotlist -W2 -fulltime). job_search.py adds an optional Adzuna API search with any filters. |
| Filters: last 24 hours, US-based | Applied to pasted posts (age tags and US location detection) and to API results (max_days_old, US endpoint). |
| Extract recruiter details using APIs | extract_posts.py and the "Extract with AI" button use the Claude API to read each post and return name, company, role, email, phone and location, with a pattern-matching fallback. job_search.py reads the Adzuna API. |
| Send emails with the team template | gmail_outreach.py fills the template ({{name}}, {{role}}, {{your_name}}, {{phone}}, {{hook}}) and sends through Gmail with a daily cap, dry-run mode, SEND confirmation, no repeat emails and an opt-out line. Threaded follow-ups, reply detection and a report are included. |

## Files
- index.html          The web interface (paste posts, filters, recruiter table, template editor, drafts, follow-up tracker, do-not-contact list, CSV download).
- app.py              Local server for the interface, Adzuna search, AI extraction and LinkedIn sign-in.
- job_search.py       Adzuna job search with any filters. Saves recruiters.csv. `--preset c2c` runs the team's search.
- extract_posts.py    AI extractor for pasted posts. Saves recruiters.csv.
- gmail_outreach.py   Send, follow up, check replies and report, through Gmail.
- sample_posts.txt    Example posts for testing extract_posts.py.
- do_not_contact.txt  Emails that must never be contacted.

## Run order
1. python app.py   (opens http://127.0.0.1:8000)
2. In the page: Open LinkedIn search, copy posts, paste them in, Extract recruiters (or Extract with AI).
3. Edit the template, then Download recruiters.csv and put it in this folder.
4. python gmail_outreach.py send --candidate "Your Name" --skills "Java,Spring Boot" --dry-run
5. python gmail_outreach.py send --candidate "Your Name" --skills "Java,Spring Boot"
6. python gmail_outreach.py followup --candidate "Your Name" --days 7
7. python gmail_outreach.py replies
8. python gmail_outreach.py report

Optional: `python job_search.py --preset c2c` for Adzuna results, and `python extract_posts.py --in sample_posts.txt`.

## Keys and settings (environment variables, never put them in files)
- ADZUNA_APP_ID, ADZUNA_APP_KEY                     developer.adzuna.com (a new account has only a few test calls)
- ANTHROPIC_API_KEY                                 optional, console.anthropic.com
- GMAIL_EMAIL, GMAIL_APP_PASSWORD                   Google Account > Security > App passwords
- LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET        optional, linkedin.com/developers/apps
  Redirect URL to add in the LinkedIn app: http://127.0.0.1:8000/auth/linkedin/callback

## Safeguards
Emails are previewed before sending, capped at 20 a day by default, never repeated to the same recruiter, and always end with an opt-out line. Anyone in do_not_contact.txt is skipped. The personal line in each email only mentions skills that appear both in the job post and in your own --skills list.

## Not included
Automatic LinkedIn password login and scraping (it breaks LinkedIn's terms and risks account bans) and AI resume rewriting (it can add experience a candidate does not have).

## Limits
Adzuna lists job-board postings, which rarely contain recruiter emails, so recruiter extraction works best from LinkedIn posts that you copy yourself. Results depend on what the posts contain.
