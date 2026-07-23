# Q2937: Accept stale finalization in get_payout_tx_blockhash_derivation

## Question
Can an unprivileged attacker replay or delay the claimed `input_outpoint` through public gRPC `ClementineAggregator.Withdraw` request so `get_payout_tx_blockhash_derivation` acts on stale finalization state after the canonical context already changed, corrupting the withdrawal-to-output binding and breaking the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/bitvm_client.rs::get_payout_tx_blockhash_derivation
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the claimed `input_outpoint`
- Exploit idea: reuse old the claimed `input_outpoint` after a newer canonical context already exists
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
