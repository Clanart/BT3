# Q2910: NEAR add_fast_transfer fast-transfer status changes in the wrong order through cross-module drift

## Question
Can an unprivileged attacker use `internal state writer reached from public fast-finalization flows` with control over fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id and desynchronize `near/omni-bridge/src/lib.rs::add_fast_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fast-transfer status changes in the wrong order` attack class because persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::add_fast_transfer` and the adjacent replay-protection bookkeeping after every branch.
