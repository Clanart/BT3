# Q959: Race internal_get_emergency_stop_tx across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public gRPC `ClementineAggregator.InternalGetEmergencyStopTx` request interactions around the streamed nonce-session identifiers and public nonce ordering so `internal_get_emergency_stop_tx` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, and leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/aggregator.rs::internal_get_emergency_stop_tx
- Entrypoint: public gRPC `ClementineAggregator.InternalGetEmergencyStopTx` request
- Attacker controls: the streamed nonce-session identifiers and public nonce ordering
- Exploit idea: use retries, batching, or timing around the streamed nonce-session identifiers and public nonce ordering to desynchronize state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
