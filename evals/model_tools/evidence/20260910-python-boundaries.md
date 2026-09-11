# Python boundary investigation: archived summary

Investigation date: 2026-09-10.
Reported environment: CPython 3.13.12 with Pydantic 2.13.5.

This summary was relocated from `docs/ideal-state.md` under P-STATE-004. It
preserves that document's reported observations; relocation did not rerun the
investigation or add new experimental evidence.

The investigation reported live existing-cell and global rebinding,
global/nonlocal writeback, and unbound-name errors in the implementation. An
outer variable mentioned only in Natural text instead retained its
decoration-time value after the outer variable was rebound.

The [target source contract](../../../docs/ideal-state.md#supported-python-source-forms)
requires preserving existing cells and live globals, and removes the stale
Natural-only capture behavior. These observations do not establish full target
annotation acquisition, production readiness, or cross-backend correctness.
