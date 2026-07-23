# Q2850: Misbind trusted context inside aggregator_deposit_movetx_lands_onchain

## Question
Can an unprivileged attacker reach `aggregator_deposit_movetx_lands_onchain` through public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes and make attacker-controlled the claimed `deposit_outpoint` bound to that raw move transaction bind to the wrong trusted context, so the move-to-vault transaction accepted for a deposit is interpreted for one bridge action while authorizing another, violating the invariant that the raw move transaction must be structurally valid and bound to the exact deposit it finalizes, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/aggregator.rs::aggregator_deposit_movetx_lands_onchain
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the claimed `deposit_outpoint` bound to that raw move transaction
- Exploit idea: bind attacker-controlled the claimed `deposit_outpoint` bound to that raw move transaction to the wrong trusted bridge context
- Invariant to test: the raw move transaction must be structurally valid and bound to the exact deposit it finalizes
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
