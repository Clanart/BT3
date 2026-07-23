# Q3236: Accept stale finalization in get_payout_info_from_move_txid

## Question
Can an unprivileged attacker replay or delay the requested `output_script_pubkey` through public gRPC `ClementineAggregator.Withdraw` request so `get_payout_info_from_move_txid` acts on stale finalization state after the canonical context already changed, corrupting the payout destination or payout amount and breaking the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/verifier.rs::get_payout_info_from_move_txid
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the requested `output_script_pubkey`
- Exploit idea: reuse old the requested `output_script_pubkey` after a newer canonical context already exists
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
