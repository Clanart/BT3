# Q973: Misbind trusted context inside withdraw

## Question
Can an unprivileged attacker reach `withdraw` through public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` and make attacker-controlled the requested `output_script_pubkey` bind to the wrong trusted context, so the withdrawal-to-output binding is interpreted for one bridge action while authorizing another, violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/operator.rs::withdraw
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the requested `output_script_pubkey`
- Exploit idea: bind attacker-controlled the requested `output_script_pubkey` to the wrong trusted bridge context
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
