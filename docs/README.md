# Documentation

Start with the [project README](../README.md) for the overview.

## Guides
| Guide | For |
|---|---|
| [Architecture](ARCHITECTURE.md) | The services, the data, one call end to end, the security model |
| [Integrating with your environment](INTEGRATION-GUIDE.md) | Loading call lists from an EHR, auditing them, exporting results (Python examples) |
| [Telephony and SMS providers](TELEPHONY-PROVIDERS.md) | Using a provider other than Twilio |
| [Deploy](DEPLOY.md) | Standing up a new environment |
| [Runbook](RUNBOOK.md) | Operating it |
| [Editing questions and languages](EDITING-QUESTIONS.md) | Changing wording, questions, triage rules and translations |

## Reference
| Document | Covers |
|---|---|
| [EHR scheduling API](INGEST-API.md) | `/api/v1/call-batches` contract |
| [Knowledge base API](KNOWLEDGE-API.md) | Admin routes for the optional knowledge base |
| [Configuration](CONFIG.md) | Every configuration key and environment variable (generated) |
| [Voice evaluations](evals/) | Results of simulated-patient evaluation runs |

## Design notes
Why the system is built the way it is. They are kept up to date with a status line at the top of each.

| Note | Topic |
|---|---|
| [Use cases](USE-CASES-PLAN.md) | Use cases as versioned specs on a generic engine (post-op, pre-op, …) |
| [Languages](LANGUAGES-PLAN.md) | The 24 languages, the safety layer, language packs, bilingual transcripts |
| [Knowledge base](KNOWLEDGE-PLAN.md) | Grounded answers from approved content: off, shadow and live modes |
| [Analytics](ANALYTICS-PLAN.md) | De-identified BigQuery export and the admin Analytics section |
| [Consoles, scheduling API, branding](ADMIN-PLAN.md) | The nurse/admin split and EHR scheduling |

## History
| Document | Covers |
|---|---|
| [Original plan](PLAN.md) | The first build plan and its decisions and risks |
| [Implementation phases](IMPLEMENTATION.md) | How the first version was built |
| [Development log](HANDOFF.md) | A dated record of changes, with what went wrong and why things were fixed the way they were |
| [Voice spike](spikes/voice.md) | Phase-0 results |
