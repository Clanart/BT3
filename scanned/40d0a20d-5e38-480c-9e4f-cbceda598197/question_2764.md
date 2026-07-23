# Q2764: Confuse actor or dependency selection in get_payout_info_from_move_txid

## Question
Can an unprivileged attacker manipulate the claimed `input_outpoint` via public gRPC `ClementineAggregator.Withdraw` request so `get_payout_info_from_move_txid` selects the wrong operator, signer, fee payer, or dependency path, corrupting the withdrawal-to-output binding and violating the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/verifier.rs::get_payout_info_from_move_txid
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the claimed `input_outpoint`
- Exploit idea: push the wrong operator, signer, fee payer, or dependency path using the claimed `input_outpoint`
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
