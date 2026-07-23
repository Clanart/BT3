# Q1547: Leave reusable partial state in create_reimburse_txhandler

## Question
Can an unprivileged attacker force a partial failure around the `deposit_outpoint` and its on-chain prevout details and then resume public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline so `create_reimburse_txhandler` continues from stale intermediate state, causing the reimbursement path that must remain slashable and recoverable to diverge from the canonical bridge context and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/builder/transaction/operator_reimburse.rs::create_reimburse_txhandler
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: force a partial failure around the `deposit_outpoint` and its on-chain prevout details and then resume under changed state
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
