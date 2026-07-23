# Q3349: Bypass settlement gating in parse_withdrawal_sig_params

## Question
Can an unprivileged attacker craft the user `input_signature` so `parse_withdrawal_sig_params` satisfies its local gating checks for the wrong bridge action, corrupting the collateral or bridge-controlled UTXO chosen for settlement and violating the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/parser/operator.rs::parse_withdrawal_sig_params
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the user `input_signature`
- Exploit idea: make local checks pass for the wrong bridge action via the user `input_signature`
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
