# Q3474: NEAR add_fast_transfer fast-transfer storage refund reaches wrong party through cross-module drift

## Question
Can an unprivileged attacker use `internal state writer reached from public fast-finalization flows` with control over fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id and desynchronize `near/omni-bridge/src/lib.rs::add_fast_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fast-transfer storage refund reaches wrong party` attack class because persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target stored `storage_owner` values and removal paths that issue refunds after relayer activity. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Create relayer and user combinations and assert that every refund lands on the exact payer who financed that fast-transfer slot. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::add_fast_transfer` and the adjacent replay-protection bookkeeping after every branch.
