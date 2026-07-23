# Q1076: Misbind trusted context inside create_payout_txhandler

## Question
Can an unprivileged attacker reach `create_payout_txhandler` through public gRPC `ClementineAggregator.Withdraw` request and make attacker-controlled the user `input_signature` bind to the wrong trusted context, so the withdrawal-to-output binding is interpreted for one bridge action while authorizing another, violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/builder/transaction/operator_reimburse.rs::create_payout_txhandler
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the user `input_signature`
- Exploit idea: bind attacker-controlled the user `input_signature` to the wrong trusted bridge context
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
