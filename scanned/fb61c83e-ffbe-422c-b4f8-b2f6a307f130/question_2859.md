# Q2859: Accept stale finalization in internal_finalized_payout

## Question
Can an unprivileged attacker replay or delay the requested `output_amount` through public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` so `internal_finalized_payout` acts on stale finalization state after the canonical context already changed, corrupting the payout destination or payout amount and breaking the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/operator.rs::internal_finalized_payout
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the requested `output_amount`
- Exploit idea: reuse old the requested `output_amount` after a newer canonical context already exists
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
