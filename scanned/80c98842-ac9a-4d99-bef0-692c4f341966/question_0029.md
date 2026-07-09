# Q29: NEAR add_fast_transfer same fee collectible twice

## Question
Can an unprivileged attacker reach `internal state writer reached from public fast-finalization flows` and make `near/omni-bridge/src/lib.rs::add_fast_transfer` remove or preserve fee-bearing state in a way that allows the same fee to be claimed twice, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Target pending-transfer cleanup, fast-transfer removal, and replay protection around fee-claim proofs.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Balance manipulation
- Fast validation: Claim once, then replay or mutate non-economic proof fields and assert that no second claim succeeds or blocks unrelated transfers.
