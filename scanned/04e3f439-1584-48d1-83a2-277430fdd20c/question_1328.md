# Q1328: Race insert_signed_emergency_stop_tx_if_not_exists across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline interactions around the `deposit_outpoint` and its on-chain prevout details so `insert_signed_emergency_stop_tx_if_not_exists` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, and leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/aggregator.rs::insert_signed_emergency_stop_tx_if_not_exists
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: use retries, batching, or timing around the `deposit_outpoint` and its on-chain prevout details to desynchronize state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
