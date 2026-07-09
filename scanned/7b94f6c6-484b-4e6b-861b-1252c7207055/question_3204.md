# Q3204: NEAR add_fast_transfer fast-transfer storage refund reaches wrong party

## Question
Can an unprivileged attacker exploit `internal state writer reached from public fast-finalization flows` so that `near/omni-bridge/src/lib.rs::add_fast_transfer` refunds reserved fast-transfer storage to the wrong account because of persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target stored `storage_owner` values and removal paths that issue refunds after relayer activity.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Create relayer and user combinations and assert that every refund lands on the exact payer who financed that fast-transfer slot.
