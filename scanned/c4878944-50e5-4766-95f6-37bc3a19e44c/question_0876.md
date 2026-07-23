# Q876: Break signature/domain separation in get_payout_info_from_move_txid

## Question
Can an unprivileged attacker use public gRPC `ClementineAggregator.Withdraw` request with crafted the selected operator x-only public-key list to defeat the message-boundary assumptions inside `get_payout_info_from_move_txid`, so an authorization that should only apply to one context also applies to another, corrupting the payout destination or payout amount and violating the invariant that operator selection and reimbursement state must not let one user request settle another user context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/database/verifier.rs::get_payout_info_from_move_txid
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the selected operator x-only public-key list
- Exploit idea: defeat message-boundary assumptions around the selected operator x-only public-key list
- Invariant to test: operator selection and reimbursement state must not let one user request settle another user context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
