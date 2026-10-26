TZ2 Hidden Opportunities cleanup

Replace only these two files in your existing project:
  hidden_opportunities/hidden_monitor.py
  hidden_opportunities/hidden_sources.json

Do NOT replace job_monitor.py.
Do NOT delete hidden_seen.json in production.

Fixes:
- rejects category/landing pages masquerading as vacancies
- requires concrete vacancy evidence (JobPosting metadata / strong job URL / job metadata)
- cleans leaked location text
- foreign geography -> LOW/IGNORE
- generic Manager titles no longer auto-HIGH/APPLY
- Finnish locations like Vaasa, FI recognized as Finland
- non-job rejections no longer counted as parser errors
- KONE source URL updated to official kone.com careers page
