# Q989: Misbind trusted context inside parse_withdrawal_sig_params

## Question
Can an unprivileged attacker reach `parse_withdrawal_sig_params` through public gRPC `ClementineAggregator.Withdraw` request and make attacker-controlled the requested `output_amount` bind to the wrong trusted context, so the collateral or bridge-controlled UTXO chosen for settlement is interpreted for one bridge action while authorizing another, violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/parser/operator.rs::parse_withdrawal_sig_params
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the requested `output_amount`
- Exploit idea: bind attacker-controlled the requested `output_amount` to the wrong trusted bridge context
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
