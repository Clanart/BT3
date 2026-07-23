# Q531: Break signature/domain separation in transfer_outpoints_to_wallet

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw` with crafted the selected operator x-only public-key list to defeat the message-boundary assumptions inside `transfer_outpoints_to_wallet`, so an authorization that should only apply to one context also applies to another, corrupting the collateral or bridge-controlled UTXO chosen for settlement and violating the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/operator.rs::transfer_outpoints_to_wallet
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request or auth-bypass attempt into `ClementineOperator.Withdraw`
- Attacker controls: the selected operator x-only public-key list
- Exploit idea: defeat message-boundary assumptions around the selected operator x-only public-key list
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
