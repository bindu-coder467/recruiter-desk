"""Gmail outreach for recruiters.csv: send, follow up, sync replies, report.

Uses your own Gmail account over SMTP/IMAP with an App Password
(Google Account > Security > 2-Step Verification > App passwords).

  set GMAIL_EMAIL=you@gmail.com
  set GMAIL_APP_PASSWORD=abcd efgh ijkl mnop

Commands
  python gmail_outreach.py send     --candidate "Your Name" --skills "Java,Spring Boot,AWS" --dry-run
  python gmail_outreach.py send     --candidate "Your Name" --skills "Java,Spring Boot,AWS" --resume resume.pdf
  python gmail_outreach.py followup --candidate "Your Name" --days 7
  python gmail_outreach.py replies
  python gmail_outreach.py report

Safeguards: daily cap (default 20), no repeat emails to the same recruiter,
dry-run mode, a typed SEND confirmation, a delay between emails and an
opt-out line on every message.
"""
import argparse
import csv
import imaplib
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import make_msgid, parseaddr

RECRUITERS = "recruiters.csv"
HISTORY = "email_sent_history.csv"
HFIELDS = ["recruiter_name", "recruiter_email", "role", "post_url", "candidate", "email_sent_status",
           "timestamp", "subject", "message_id", "followup_count", "last_followup_at", "replied"]
OPT_OUT = "\n\n--\nNot relevant? Reply \"stop\" and I will not contact you again."
DEFAULT_TEMPLATE = """Subject: {{role}} opportunity

Hi {{name}},

I saw your post about the {{role}} role. {{hook}}

I would be glad to share a resume and my availability. What is the best way to send it over?

Thanks,
{{your_name}}
{{phone}}"""


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def dnc():
    """Emails listed in do_not_contact.txt (one per line, # for comments) are never contacted."""
    if not os.path.exists("do_not_contact.txt"):
        return set()
    with open("do_not_contact.txt", encoding="utf-8") as f:
        return {l.strip().lower() for l in f if l.strip() and not l.startswith("#")}


def save_history(rows):
    with open(HISTORY, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HFIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in HFIELDS})  # also migrates old headers


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def history():
    rows = load(HISTORY)
    for r in rows:
        r["followup_count"] = r.get("followup_count") or "0"
        r["replied"] = r.get("replied") or "0"
    return rows


def creds():
    u, p = os.getenv("GMAIL_EMAIL"), os.getenv("GMAIL_APP_PASSWORD")
    if not u or not p:
        sys.exit("Set GMAIL_EMAIL and GMAIL_APP_PASSWORD first.")
    return u, p.replace(" ", "")


def hook(jd, skills):
    """Only mention skills that are BOTH in the job text and in your own skills list."""
    hit = [s for s in skills if re.search(r"(?<!\w)" + re.escape(s) + r"(?!\w)", jd, re.I)][:3]
    if not hit:
        return ""
    joined = hit[0] if len(hit) == 1 else ", ".join(hit[:-1]) + " and " + hit[-1]
    return f"My experience with {joined} lines up with what you are looking for."


def render(text, ctx):
    for k, v in ctx.items():
        text = text.replace("{{" + k + "}}", v or "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def load_template(path):
    raw = open(path, encoding="utf-8").read() if path else DEFAULT_TEMPLATE
    head, _, body = raw.partition("\n\n")
    return head.replace("Subject:", "", 1).strip(), body


def connect(user, pw):
    ctx = ssl.create_default_context()
    try:
        s = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
        s.starttls(context=ctx)
    except Exception:
        s = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30, context=ctx)
    s.login(user, pw)
    return s


def build(user, to, subject, body, a, resume=None, thread=None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = user, to, subject
    mid = make_msgid(domain=user.split("@")[-1])
    m["Message-ID"] = mid
    if a.cc:
        m["Cc"] = a.cc
    if thread:
        m["In-Reply-To"] = m["References"] = thread
    m.set_content(body + OPT_OUT)
    if resume:
        with open(resume, "rb") as f:
            name = re.sub(r"[^\x20-\x7e]", "", os.path.basename(resume)) or "resume.pdf"
            m.add_attachment(f.read(), maintype="application", subtype="octet-stream", filename=name)
    rcpts = [to] + [x.strip() for x in (a.cc or "").split(",") if x.strip()] + \
            [x.strip() for x in (a.bcc or "").split(",") if x.strip()]
    return m, mid, rcpts


def sent_today(rows, candidate):
    d = datetime.now().strftime("%Y-%m-%d")
    return sum(1 for r in rows if r["candidate"] == candidate and r["timestamp"].startswith(d)
               and r["email_sent_status"] in ("sent", "followup"))


def cmd_send(a):
    rows, hist = load(RECRUITERS), history()
    done = {(r["candidate"], r["recruiter_email"].lower()) for r in hist}
    skills = [s.strip() for s in (a.skills or "").split(",") if s.strip()]
    subj_t, body_t = load_template(a.template)
    room = max(0, min(a.limit or a.cap, a.cap - sent_today(hist, a.candidate)))
    blocked = dnc()
    todo = [r for r in rows if r.get("recruiter_email") and r["recruiter_email"].lower() not in blocked and
            (a.candidate, r["recruiter_email"].lower()) not in done][:room]
    print(f"{len(todo)} to send (cap {a.cap}/day, {sent_today(hist, a.candidate)} already sent today).")
    if not todo:
        return

    def compose(r):
        ctx = {"name": (r.get("recruiter_name") or "there").split()[0], "role": r.get("role", "the"),
               "your_name": a.candidate, "phone": a.phone or "",
               "hook": hook(r.get("job_description", ""), skills)}
        return render(subj_t, ctx), render(body_t, ctx)

    if a.dry_run:
        for r in todo:
            s, b = compose(r)
            print(f"\nTo: {r['recruiter_email']}\nSubject: {s}\n\n{b}{OPT_OUT}\n{'-' * 40}")
        print("Dry run only. Nothing was sent.")
        return
    if input("Type SEND to send these emails: ").strip() != "SEND":
        return print("Cancelled.")
    user, pw = creds()
    smtp = connect(user, pw)
    for i, r in enumerate(todo):
        s, b = compose(r)
        row = {"recruiter_name": r.get("recruiter_name"), "recruiter_email": r["recruiter_email"].lower(),
               "role": r.get("role"), "post_url": r.get("post_url"), "candidate": a.candidate,
               "subject": s, "timestamp": now(), "followup_count": "0", "replied": "0"}
        try:
            m, mid, rcpts = build(user, r["recruiter_email"], s, b, a, a.resume)
            smtp.send_message(m, to_addrs=rcpts)
            row.update(email_sent_status="sent", message_id=mid)
            print(f"sent  {r['recruiter_email']}")
        except Exception as e:
            row.update(email_sent_status="failed")
            print(f"FAIL  {r['recruiter_email']}: {e}")
        hist.append(row)
        save_history(hist)
        if i < len(todo) - 1:
            time.sleep(a.delay)
    smtp.quit()


def cmd_followup(a):
    hist = history()
    cutoff = datetime.now() - timedelta(days=a.days)
    due = [r for r in hist if r["candidate"] == a.candidate and r["email_sent_status"] == "sent"
           and r["followup_count"] == "0" and r["replied"] == "0" and r["message_id"]
           and r["recruiter_email"].lower() not in dnc()
           and datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S") <= cutoff]
    due = due[:max(0, a.cap - sent_today(hist, a.candidate))]
    print(f"{len(due)} follow-ups due (no reply after {a.days} days).")
    if not due or a.dry_run:
        for r in due:
            print(f"  would follow up: {r['recruiter_email']}")
        return
    if input("Type SEND to send these follow-ups: ").strip() != "SEND":
        return print("Cancelled.")
    user, pw = creds()
    smtp = connect(user, pw)
    for i, r in enumerate(due):
        name = (r["recruiter_name"] or "there").split()[0]
        body = (f"Hi {name},\n\nFollowing up on my note from {a.days} days ago about the {r['role']} role. "
                f"I am happy to share my resume whenever it is useful.\n\nThanks,\n{a.candidate}\n{a.phone or ''}")
        try:
            m, mid, rcpts = build(user, r["recruiter_email"], "Re: " + r["subject"], body.strip(), a,
                                  a.resume, thread=r["message_id"])
            smtp.send_message(m, to_addrs=rcpts)
            r["followup_count"], r["last_followup_at"] = "1", now()
            print(f"followed up  {r['recruiter_email']}")
        except Exception as e:
            print(f"FAIL  {r['recruiter_email']}: {e}")
        save_history(hist)
        if i < len(due) - 1:
            time.sleep(a.delay)
    smtp.quit()


def cmd_replies(a):
    hist = history()
    pending = {r["recruiter_email"].lower(): r for r in hist if r["replied"] == "0" and r["email_sent_status"] == "sent"}
    if not pending:
        return print("No pending emails to check.")
    oldest = min(datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S") for r in pending.values())
    user, pw = creds()
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    imap.login(user, pw)
    imap.select("INBOX", readonly=True)
    _, data = imap.search(None, "SINCE", oldest.strftime("%d-%b-%Y"))
    ids, found = data[0].split(), 0
    for i in range(0, len(ids), 100):
        batch = b",".join(ids[i:i + 100])
        _, msgs = imap.fetch(batch, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
        for part in msgs:
            if isinstance(part, tuple):
                sender = parseaddr(BytesParser().parsebytes(part[1], headersonly=True)["From"] or "")[1].lower()
                if sender in pending and pending[sender]["replied"] == "0":
                    pending[sender]["replied"] = "1"
                    found += 1
    imap.logout()
    save_history(hist)
    print(f"{found} new repl{'y' if found == 1 else 'ies'} found.")


def cmd_report(a):
    h = [r for r in history() if r["email_sent_status"] == "sent"]
    rep = sum(1 for r in h if r["replied"] == "1")
    fu = sum(1 for r in h if r["followup_count"] != "0")
    print(f"Sent: {len(h)}\nReplied: {rep} ({round(rep / len(h) * 100) if h else 0}%)\nFollow-ups sent: {fu}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--candidate", default="")
    common.add_argument("--phone", default="")
    common.add_argument("--resume", help="your real resume file to attach, sent as is")
    common.add_argument("--cc", default="")
    common.add_argument("--bcc", default="")
    common.add_argument("--cap", type=int, default=20, help="max emails per day (default 20)")
    common.add_argument("--delay", type=float, default=3, help="seconds between emails")
    common.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("send", parents=[common])
    s.add_argument("--skills", help="your real skills, comma separated, used for the personal line")
    s.add_argument("--template", help="text file: Subject: line, blank line, body")
    s.add_argument("--limit", type=int)
    f = sub.add_parser("followup", parents=[common])
    f.add_argument("--days", type=int, default=7)
    sub.add_parser("replies")
    sub.add_parser("report")
    a = p.parse_args()
    if a.cmd in ("send", "followup") and not a.candidate:
        p.error("--candidate is required")
    {"send": cmd_send, "followup": cmd_followup, "replies": cmd_replies, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
