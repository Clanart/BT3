# Q2375: Cross-wire presigning material in internal_get_emergency_stop_tx

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.InternalGetEmergencyStopTx` request to make `internal_get_emergency_stop_tx` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the `deposit_outpoint` and its on-chain prevout details, so the reimbursement path that must remain slashable and recoverable is authorized under the wrong context and the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind breaks, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/aggregator.rs::internal_get_emergency_stop_tx
- Entrypoint: public gRPC `ClementineAggregator.InternalGetEmergencyStopTx` request
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
