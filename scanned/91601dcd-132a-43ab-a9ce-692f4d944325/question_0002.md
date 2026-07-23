# Q2: Accept wrong transaction/proof shape in aggregator_two_deposit_movetx_and_emergency_stop

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes with malformed the transaction witness, anchor output, and script-path structure so `aggregator_two_deposit_movetx_and_emergency_stop` passes shallow structural checks while changing the transaction or proof semantics that later settlement relies on, corrupting the move-to-vault transaction accepted for a deposit and violating the invariant that the raw move transaction must be structurally valid and bound to the exact deposit it finalizes, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/aggregator.rs::aggregator_two_deposit_movetx_and_emergency_stop
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the transaction witness, anchor output, and script-path structure
- Exploit idea: pass shallow structural checks while changing later settlement semantics using the transaction witness, anchor output, and script-path structure
- Invariant to test: the raw move transaction must be structurally valid and bound to the exact deposit it finalizes
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
