# Q1529: Leave reusable partial state in create_watchtower_challenge_timeout_txhandler

## Question
Can an unprivileged attacker force a partial failure around the `old_move_txid` in `ReplacementDeposit` and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `create_watchtower_challenge_timeout_txhandler` continues from stale intermediate state, causing the deposit-to-move-tx binding to diverge from the canonical bridge context and breaking the invariant that partial pipeline failures must not leave reusable or cross-bindable signing state behind, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/builder/transaction/challenge.rs::create_watchtower_challenge_timeout_txhandler
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: force a partial failure around the `old_move_txid` in `ReplacementDeposit` and then resume under changed state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
