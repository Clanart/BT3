# Q1365: NEAR add_fast_transfer fast path changes fee semantics without changing proof identity

## Question
Can an unprivileged attacker use `internal state writer reached from public fast-finalization flows` to create a fast-transfer state whose effective fee differs from the fee later proven and claimed via `near/omni-bridge/src/lib.rs::add_fast_transfer`, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target relayer-sponsored fast paths where the first leg is paid before the canonical proof arrives.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare claimed fee, relayer payout, and stored transfer fee across both legs and assert that the bridge never accepts a mismatch.
