# Q3435: Break reimbursement recoverability in create_reimburse_txhandler

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline with crafted the `old_move_txid` in `ReplacementDeposit` so `create_reimburse_txhandler` moves the protocol past the point where reimbursement should remain recoverable, leaving the operator signature set attached to a deposit inconsistent with the assumption that partial pipeline failures must not leave reusable or cross-bindable signing state behind, and leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/builder/transaction/operator_reimburse.rs::create_reimburse_txhandler
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: move bridge state forward while reimbursement/slashability stays tied to older state
- Invariant to test: partial pipeline failures must not leave reusable or cross-bindable signing state behind
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
