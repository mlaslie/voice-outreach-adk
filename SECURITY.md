# Security policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead:
- report them privately to the repository maintainers (GitHub: **Security → Report a vulnerability**, or email the maintainers listed for your distribution);
- include the steps to reproduce, the affected component (gateway, API, UI, nurse agent, analytics, infrastructure), and the impact.

We aim to acknowledge reports within five working days.

## Scope notes for deployers

**What this system handles.** It processes protected health information: names, phone numbers, dates of birth, answers to clinical questions, call recordings and transcripts.

**What the code does to protect it:**
- **One public service.** The only public service is the telephony gateway, and every route on it authenticates its caller: a provider signature, or an HMAC token.
- **Private API with per-route roles.** The API is private and checks the calling service identity route by route. The staff consoles sit behind Identity-Aware Proxy.
- **Secrets.** Secrets live in Secret Manager and are never in code or Terraform state.
- **No PHI in logs.** Logs carry identifiers and field names, never patient answers.
- **No PHI in the repository.** The repository contains no patient data or real configuration; a PII scanner and a secret scanner run on every commit.

**What you are responsible for when you deploy:**
- your IAM bindings;
- the IAP access lists;
- data retention, including the recordings bucket lifecycle and Firestore TTLs;
- VPC Service Controls and CMEK, if your policies require them;
- agreements (BAAs) with Google Cloud and with your telephony and SMS providers.
