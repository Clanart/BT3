# Q1004: Misbind trusted context inside get_reimbursement_txs

## Question
Can an unprivileged attacker reach `get_reimbursement_txs` through public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` and make attacker-controlled the requested `output_amount` bind to the wrong trusted context, so the operator selection or reimbursement state for the withdrawal is interpreted for one bridge action while authorizing another, violating the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/operator.rs::get_reimbursement_txs
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the requested `output_amount`
- Exploit idea: bind attacker-controlled the requested `output_amount` to the wrong trusted bridge context
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
