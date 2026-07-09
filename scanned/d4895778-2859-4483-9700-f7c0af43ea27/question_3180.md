# Q3180: NEAR init_transfer resume path same fee collectible twice

## Question
Can an unprivileged attacker reach `yield-resume callback for a previously deferred outbound transfer` and make `near/omni-bridge/src/lib.rs::init_transfer_resume` remove or preserve fee-bearing state in a way that allows the same fee to be claimed twice, violating `resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_resume`
- Entrypoint: `yield-resume callback for a previously deferred outbound transfer`
- Attacker controls: timing of storage funding, promise completion order, storage owner, message-storage account state, and the original transfer message contents
- Exploit idea: Target pending-transfer cleanup, fast-transfer removal, and replay protection around fee-claim proofs.
- Invariant to test: resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing
- Expected Immunefi impact: Balance manipulation
- Fast validation: Claim once, then replay or mutate non-economic proof fields and assert that no second claim succeeds or blocks unrelated transfers.
