# Q1848: NEAR add_fast_transfer fast path changes fee semantics without changing proof identity at boundary values

## Question
Can an unprivileged attacker trigger `internal state writer reached from public fast-finalization flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::add_fast_transfer` violate `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs` in the `fast path changes fee semantics without changing proof identity` attack class because persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target relayer-sponsored fast paths where the first leg is paid before the canonical proof arrives. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare claimed fee, relayer payout, and stored transfer fee across both legs and assert that the bridge never accepts a mismatch. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
