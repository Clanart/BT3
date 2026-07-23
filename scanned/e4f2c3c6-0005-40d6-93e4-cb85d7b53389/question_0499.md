# Q499: Break signature/domain separation in internal_finalized_payout

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` with crafted the retry / batching / timing of repeated withdrawal requests to defeat the message-boundary assumptions inside `internal_finalized_payout`, so an authorization that should only apply to one context also applies to another, corrupting the payout destination or payout amount and violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/operator.rs::internal_finalized_payout
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the retry / batching / timing of repeated withdrawal requests
- Exploit idea: defeat message-boundary assumptions around the retry / batching / timing of repeated withdrawal requests
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
