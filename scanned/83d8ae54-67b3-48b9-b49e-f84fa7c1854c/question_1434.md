# Q1434: Exploit anchor/output assumptions in aggregator_deposit_movetx_lands_onchain

## Question
Can an unprivileged attacker shape the timing of the call relative to an in-flight or partially failed deposit finalization so `aggregator_deposit_movetx_lands_onchain` accepts an anchor, output, or script-path layout that no longer protects the intended bridge spend, corrupting the move-to-vault transaction accepted for a deposit and violating the invariant that the raw move transaction must be structurally valid and bound to the exact deposit it finalizes, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/aggregator.rs::aggregator_deposit_movetx_lands_onchain
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the timing of the call relative to an in-flight or partially failed deposit finalization
- Exploit idea: alter the anchor, output, or script-path layout using the timing of the call relative to an in-flight or partially failed deposit finalization
- Invariant to test: the raw move transaction must be structurally valid and bound to the exact deposit it finalizes
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
