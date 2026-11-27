# Repository Working Rules

Before changing behavior, refactoring, or acting on an external review:

1. Read `docs/BUSINESS_REQUIREMENTS.md`, `docs/HANDOFF.md`, and
   `docs/IMPLEMENTATION.md` (and `docs/CAXA_INTERNALS.md` when touching helper or CAXA SDK behavior).
2. Treat user-confirmed business rules and deliberate compatibility fallbacks
   as requirements, not as bugs or cleanup opportunities.
3. Keep low-level CAXA fallbacks where the business rules require them. In
   particular, do not replace the independent low-level borrow link with the
   high-level `LinkSubPart` path.
4. For complex, multi-window, or real-client tasks, update
   `docs/BUSINESS_REQUIREMENTS.md` whenever the user confirms a new rule,
   exception, workaround, or account responsibility.
5. Add or update meaningful regression tests for persisted business rules.
6. Whenever the user confirms a new requirement, exception, workaround, or
   observed behavior that affects validation, update
   `docs/VALIDATION.md` in the same change with the date, requirement,
   implementation status, and validation evidence. Mark superseded historical
   values as historical instead of presenting them as current.
