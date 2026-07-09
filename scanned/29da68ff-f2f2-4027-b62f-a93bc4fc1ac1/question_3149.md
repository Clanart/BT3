# Q3149: NEAR foreign-to-foreign bridge forwarding relayer substitution changes economic recipient at boundary values

## Question
Can an unprivileged attacker trigger `public cross-chain forward path created by `fin_transfer` with non-Near recipient` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain` violate `foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain` in the `relayer substitution changes economic recipient` attack class because maps a foreign asset into its target-chain representation and either stores or fast-resolves a new pending transfer without ever landing value on Near becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain`
- Entrypoint: `public cross-chain forward path created by `fin_transfer` with non-Near recipient`
- Attacker controls: origin token address, target chain, fee split, and fast-transfer status
- Exploit idea: Target branches where a stored fast-transfer status replaces the canonical recipient or fee recipient. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Verify that relayer substitution happens only for the exact matching transfer id and exact matching parameters of the relayed fast payout. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
