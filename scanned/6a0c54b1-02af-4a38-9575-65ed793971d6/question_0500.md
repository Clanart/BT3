# Q500: Break signature/domain separation in internal_withdraw

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` with crafted the claimed `input_outpoint` to defeat the message-boundary assumptions inside `internal_withdraw`, so an authorization that should only apply to one context also applies to another, corrupting the operator selection or reimbursement state for the withdrawal and violating the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/operator.rs::internal_withdraw
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the claimed `input_outpoint`
- Exploit idea: defeat message-boundary assumptions around the claimed `input_outpoint`
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
