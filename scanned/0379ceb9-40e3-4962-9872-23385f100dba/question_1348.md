# Q1348: Misbind trusted context inside get_payout_info_from_move_txid

## Question
Can an unprivileged attacker reach `get_payout_info_from_move_txid` through public gRPC `ClementineAggregator.Withdraw` request and make attacker-controlled the retry / batching / timing of repeated withdrawal requests bind to the wrong trusted context, so the mapping between a withdrawal and the deposit it spends against is interpreted for one bridge action while authorizing another, violating the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/database/verifier.rs::get_payout_info_from_move_txid
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the retry / batching / timing of repeated withdrawal requests
- Exploit idea: bind attacker-controlled the retry / batching / timing of repeated withdrawal requests to the wrong trusted bridge context
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
