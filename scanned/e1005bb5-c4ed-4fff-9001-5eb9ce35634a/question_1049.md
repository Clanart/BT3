# Q1049: Misbind trusted context inside get_payout_tx_blockhash_derivation

## Question
Can an unprivileged attacker reach `get_payout_tx_blockhash_derivation` through public gRPC `ClementineAggregator.Withdraw` request and make attacker-controlled the selected operator x-only public-key list bind to the wrong trusted context, so the payout destination or payout amount is interpreted for one bridge action while authorizing another, violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/bitvm_client.rs::get_payout_tx_blockhash_derivation
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the selected operator x-only public-key list
- Exploit idea: bind attacker-controlled the selected operator x-only public-key list to the wrong trusted bridge context
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
