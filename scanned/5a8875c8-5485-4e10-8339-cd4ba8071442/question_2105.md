# Q2105: NEAR foreign-to-foreign bridge forwarding fast path can pay before canonical parameters are locked

## Question
Can an unprivileged attacker use `public cross-chain forward path created by `fin_transfer` with non-Near recipient` to make `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain` release a fast-transfer payout before the canonical transfer parameters are irreversibly fixed, violating `foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain`
- Entrypoint: `public cross-chain forward path created by `fin_transfer` with non-Near recipient`
- Attacker controls: origin token address, target chain, fee split, and fast-transfer status
- Exploit idea: Target relayer-funded near-term payouts that rely on later proofs to confirm the first leg.
- Invariant to test: foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare fast-payout parameters to the later proof and assert that mismatched proofs cannot still unlock relayer fee or principal reimbursement.
