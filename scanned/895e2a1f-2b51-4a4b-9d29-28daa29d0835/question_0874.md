# Q874: Break signature/domain separation in update_withdrawal_utxo_from_citrea_withdrawal

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request with crafted the optional `verification_signature` wrapper to defeat the message-boundary assumptions inside `update_withdrawal_utxo_from_citrea_withdrawal`, so an authorization that should only apply to one context also applies to another, corrupting the payout destination or payout amount and violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/verifier.rs::update_withdrawal_utxo_from_citrea_withdrawal
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the optional `verification_signature` wrapper
- Exploit idea: defeat message-boundary assumptions around the optional `verification_signature` wrapper
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
