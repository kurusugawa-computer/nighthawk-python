## NON-NEGOTIABLE REQUIREMENTS
- Do not edit files until all questions in the current chat session are resolved and explicit user permission is granted (except ExecPlans).
- When listing questions, confirmations, or proposed decisions for the Product Owner (PO), assign a short stable Id to each item so the PO can respond inline.
  - Required format: `Q-FOO-01` (questions), `C-FOO-01` (confirmations), `P-FOO-01` (proposals). For follow-ups, append a suffix like `Q-FOO-01A`.
  - Each item must be answerable on its own and must include its Id in the text.

## ExecPlans
Only create an ExecPlan when author instruction explicitly requests it. If an ExecPlan is not explicitly requested, do not use ExecPlan files, even for large or risky changes.

When an ExecPlan is explicitly requested, author it following `.agent/PLANS.md` before you touch code.

Treat the ExecPlan as both an executable specification and a transparency artifact: keep `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` updated as work proceeds.

Store ExecPlans under `.agent/execplans/` and filenames must be `YYYYMMDD-<slug>.md` (ASCII only, lowercase, hyphen-separated).

## Design Principles
- **Avoid premature abstraction**: Do not add classes/parameters just for hypothetical reuse; match the current call graph.
- **Naming**: Use full words in code signatures (e.g., `Reference` not `Ref`, `Options` not `Opts`) unless defined in the Glossary.
- **ASCII punctuation only**: Use `'` (U+0027) and `"` (U+0022). Do not use smart quotes.

## Glossary
- `Id` = Identifier
- `DSL` = Domain Specific Language
