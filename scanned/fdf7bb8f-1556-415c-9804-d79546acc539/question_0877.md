# Q877: Break signature/domain separation in mark_payout_handled

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request with crafted the optional `verification_signature` wrapper to defeat the message-boundary assumptions inside `mark_payout_handled`, so an authorization that should only apply to one context also applies to another, corrupting the withdrawal-to-output binding and violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/database/verifier.rs::mark_payout_handled
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the optional `verification_signature` wrapper
- Exploit idea: defeat message-boundary assumptions around the optional `verification_signature` wrapper
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
