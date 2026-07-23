# Q1819: Race update_payout_txs_and_payer_operator_xonly_pk across concurrent state

## Question
Can an unprivileged attacker batch, retry, or reorder public gRPC `ClementineAggregator.Withdraw` request interactions around the claimed `input_outpoint` so `update_payout_txs_and_payer_operator_xonly_pk` observes inconsistent state across memory, database, and on-chain checks, breaking the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, and leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/verifier.rs::update_payout_txs_and_payer_operator_xonly_pk
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the claimed `input_outpoint`
- Exploit idea: use retries, batching, or timing around the claimed `input_outpoint` to desynchronize state
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
