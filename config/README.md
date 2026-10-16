# Configuration

| File | Committed | Holds |
|---|---|---|
| `app.yaml` | yes | Defaults with no identifying values (models, timeouts, flags) |
| `site.yaml` | **no** | Project id, org name, emails, public URL |
| `providers/<name>.yaml` | **no** | Provider numbers and `secret://` references |
| `environments/<env>.yaml` | **no** | Per-environment overrides, chosen with `VOA_ENV` |
| `seed/*.yaml` | **no** | Data seeded by `scripts/seed.py` (test numbers) |
| `pii-denylist.txt` | **no** | Literals the PII scanner must never find in tracked files |

Every gitignored file has a committed `*.example.*` twin with fictional values.

Precedence: `app.yaml` → `site.yaml` → `providers/*.yaml` → `environments/$VOA_ENV.yaml`
→ `VOA__SECTION__KEY` environment variables. Values like `secret://name` are
Secret Manager references, resolved at use. For local development a secret can be
shadowed by `VOA_SECRET_<NAME>` (dashes become underscores).
