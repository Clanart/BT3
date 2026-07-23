# Q3235: Accept stale finalization in update_payout_txs_and_payer_operator_xonly_pk

## Question
Can an unprivileged attacker replay or delay the optional `verification_signature` wrapper through public gRPC `ClementineAggregator.Withdraw` request so `update_payout_txs_and_payer_operator_xonly_pk` acts on stale finalization state after the canonical context already changed, corrupting the payout destination or payout amount and breaking the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/verifier.rs::update_payout_txs_and_payer_operator_xonly_pk
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the optional `verification_signature` wrapper
- Exploit idea: reuse old the optional `verification_signature` wrapper after a newer canonical context already exists
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
