# Q2616: NEAR add_fast_transfer fast-transfer status changes in the wrong order

## Question
Can an unprivileged attacker trigger `internal state writer reached from public fast-finalization flows` so that `near/omni-bridge/src/lib.rs::add_fast_transfer` marks, removes, or reuses fast-transfer state in an order that opens replay or fee-claim gaps, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle.
