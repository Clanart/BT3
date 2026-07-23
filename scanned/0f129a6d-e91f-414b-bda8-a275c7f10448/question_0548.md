# Q548: Break signature/domain separation in update_finalized_payouts

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request that later reaches verifier tracking and challenge logic with crafted the requested `output_amount` to defeat the message-boundary assumptions inside `update_finalized_payouts`, so an authorization that should only apply to one context also applies to another, corrupting the withdrawal-to-output binding and violating the invariant that a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/verifier.rs::update_finalized_payouts
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request that later reaches verifier tracking and challenge logic
- Attacker controls: the requested `output_amount`
- Exploit idea: defeat message-boundary assumptions around the requested `output_amount`
- Invariant to test: a withdrawal signature must bind exactly one withdrawal id, one input, one output script, and one amount
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
