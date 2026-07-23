# Q2492: Confuse actor or dependency selection in create_payout_txhandler

## Question
Can an unprivileged attacker manipulate the requested `output_amount` via public gRPC `ClementineAggregator.Withdraw` request so `create_payout_txhandler` selects the wrong operator, signer, fee payer, or dependency path, corrupting the operator selection or reimbursement state for the withdrawal and violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/builder/transaction/operator_reimburse.rs::create_payout_txhandler
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the requested `output_amount`
- Exploit idea: push the wrong operator, signer, fee payer, or dependency path using the requested `output_amount`
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
