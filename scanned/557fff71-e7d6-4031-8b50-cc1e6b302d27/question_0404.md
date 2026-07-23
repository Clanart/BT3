# Q404: Replay context into get_payout_info_from_move_txid

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request with attacker-controlled the optional `verification_signature` wrapper so `get_payout_info_from_move_txid` reuses a previously accepted context, causing the withdrawal-to-output binding to be consumed twice and breaking the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/verifier.rs::get_payout_info_from_move_txid
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the optional `verification_signature` wrapper
- Exploit idea: reuse or replay previously consumed the optional `verification_signature` wrapper in a fresh context
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
