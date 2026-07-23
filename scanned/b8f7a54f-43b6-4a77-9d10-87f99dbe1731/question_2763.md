# Q2763: Confuse actor or dependency selection in update_payout_txs_and_payer_operator_xonly_pk

## Question
Can an unprivileged attacker manipulate the requested `output_amount` via public gRPC `ClementineAggregator.Withdraw` request so `update_payout_txs_and_payer_operator_xonly_pk` selects the wrong operator, signer, fee payer, or dependency path, corrupting the withdrawal-to-output binding and violating the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/verifier.rs::update_payout_txs_and_payer_operator_xonly_pk
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the requested `output_amount`
- Exploit idea: push the wrong operator, signer, fee payer, or dependency path using the requested `output_amount`
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
