# Q503: Break signature/domain separation in get_reimbursement_txs

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` with crafted the requested `output_script_pubkey` to defeat the message-boundary assumptions inside `get_reimbursement_txs`, so an authorization that should only apply to one context also applies to another, corrupting the mapping between a withdrawal and the deposit it spends against and violating the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/operator.rs::get_reimbursement_txs
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the requested `output_script_pubkey`
- Exploit idea: defeat message-boundary assumptions around the requested `output_script_pubkey`
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
