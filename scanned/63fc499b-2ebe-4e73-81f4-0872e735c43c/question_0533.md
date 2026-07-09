# Q533: NEAR add_fast_transfer same fee collectible twice at boundary values

## Question
Can an unprivileged attacker trigger `internal state writer reached from public fast-finalization flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::add_fast_transfer` violate `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs` in the `same fee collectible twice` attack class because persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target pending-transfer cleanup, fast-transfer removal, and replay protection around fee-claim proofs. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Balance manipulation
- Fast validation: Claim once, then replay or mutate non-economic proof fields and assert that no second claim succeeds or blocks unrelated transfers. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
