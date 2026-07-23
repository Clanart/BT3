# Q1820: Race get_payout_info_from_move_txid across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public gRPC `ClementineAggregator.Withdraw` request interactions around the `withdrawal_id` so `get_payout_info_from_move_txid` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, and leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/verifier.rs::get_payout_info_from_move_txid
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the `withdrawal_id`
- Exploit idea: use retries, batching, or timing around the `withdrawal_id` to desynchronize state
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
