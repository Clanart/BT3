# Q15: Replay context into internal_get_emergency_stop_tx

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.InternalGetEmergencyStopTx` request with attacker-controlled the `recovery_taproot_address` in `BaseDeposit` so `internal_get_emergency_stop_tx` reuses a previously accepted context, causing the deposit-to-move-tx binding to be consumed twice and breaking the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/aggregator.rs::internal_get_emergency_stop_tx
- Entrypoint: public gRPC `ClementineAggregator.InternalGetEmergencyStopTx` request
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: reuse or replay previously consumed the `recovery_taproot_address` in `BaseDeposit` in a fresh context
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
