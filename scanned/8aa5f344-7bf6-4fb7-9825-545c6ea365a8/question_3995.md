# Q3995: NEAR add_fast_transfer second-leg fee claim ignores first-leg mismatch through cross-module drift

## Question
Can an unprivileged attacker use `internal state writer reached from public fast-finalization flows` with control over fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id and desynchronize `near/omni-bridge/src/lib.rs::add_fast_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `second-leg fee claim ignores first-leg mismatch` attack class because persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target comparisons between stored fast-transfer state and canonical first-leg proof during fee claim. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Balance manipulation
- Fast validation: Vary principal, fee, recipient, and message between legs and assert that any mismatch blocks fee release and leaves replay state consistent. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::add_fast_transfer` and the adjacent replay-protection bookkeeping after every branch.
