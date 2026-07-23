# Q517: Break signature/domain separation in parse_withdrawal_sig_params

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request with crafted the requested `output_script_pubkey` to defeat the message-boundary assumptions inside `parse_withdrawal_sig_params`, so an authorization that should only apply to one context also applies to another, corrupting the operator selection or reimbursement state for the withdrawal and violating the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/parser/operator.rs::parse_withdrawal_sig_params
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the requested `output_script_pubkey`
- Exploit idea: defeat message-boundary assumptions around the requested `output_script_pubkey`
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
