# Q1813: Leave reusable partial state in mark_kickoff_connector_as_used

## Question
Can an unprivileged attacker force a partial failure around the `old_move_txid` in `ReplacementDeposit` and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `mark_kickoff_connector_as_used` continues from stale intermediate state, causing the operator signature set attached to a deposit to diverge from the canonical bridge context and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/operator.rs::mark_kickoff_connector_as_used
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: force a partial failure around the `old_move_txid` in `ReplacementDeposit` and then resume under changed state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
