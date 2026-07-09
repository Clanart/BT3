# Q3609: NEAR add_fast_transfer fast-transfer storage refund reaches wrong party at boundary values

## Question
Can an unprivileged attacker trigger `internal state writer reached from public fast-finalization flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::add_fast_transfer` violate `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs` in the `fast-transfer storage refund reaches wrong party` attack class because persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target stored `storage_owner` values and removal paths that issue refunds after relayer activity. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Create relayer and user combinations and assert that every refund lands on the exact payer who financed that fast-transfer slot. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
