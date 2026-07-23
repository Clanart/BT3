# Q1003: Misbind trusted context inside transfer_outpoints_to_wallet

## Question
Can an unprivileged attacker reach `transfer_outpoints_to_wallet` through public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` and make attacker-controlled the retry / batching / timing of repeated withdrawal requests bind to the wrong trusted context, so the withdrawal-to-output binding is interpreted for one bridge action while authorizing another, violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/operator.rs::transfer_outpoints_to_wallet
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the retry / batching / timing of repeated withdrawal requests
- Exploit idea: bind attacker-controlled the retry / batching / timing of repeated withdrawal requests to the wrong trusted bridge context
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
