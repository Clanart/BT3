# Q2863: Accept stale finalization in get_reimbursement_txs

## Question
Can an unprivileged attacker replay or delay the `withdrawal_id` through public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` so `get_reimbursement_txs` acts on stale finalization state after the canonical context already changed, corrupting the mapping between a withdrawal and the deposit it spends against and breaking the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/operator.rs::get_reimbursement_txs
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the `withdrawal_id`
- Exploit idea: reuse old the `withdrawal_id` after a newer canonical context already exists
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
