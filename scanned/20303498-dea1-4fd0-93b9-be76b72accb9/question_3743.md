# Q3743: NEAR add_fast_transfer second-leg fee claim ignores first-leg mismatch

## Question
Can an unprivileged attacker use `internal state writer reached from public fast-finalization flows` so that `near/omni-bridge/src/lib.rs::add_fast_transfer` lets a relayer claim fee for a fast transfer even though the first leg finalized with different parameters, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target comparisons between stored fast-transfer state and canonical first-leg proof during fee claim.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Balance manipulation
- Fast validation: Vary principal, fee, recipient, and message between legs and assert that any mismatch blocks fee release and leaves replay state consistent.
