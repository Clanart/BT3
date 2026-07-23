# Q1574: Leave reusable partial state in is_utxo_spent

## Question
Can an unprivileged attacker force a partial failure around the `deposit_outpoint` and its on-chain prevout details and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `is_utxo_spent` continues from stale intermediate state, causing the emergency-stop transaction that should protect the same deposit to diverge from the canonical bridge context and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/builder/block_cache.rs::is_utxo_spent
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: force a partial failure around the `deposit_outpoint` and its on-chain prevout details and then resume under changed state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
