# Q2708: NEAR foreign-to-foreign bridge forwarding relayer substitution changes economic recipient

## Question
Can an unprivileged attacker exploit `public cross-chain forward path created by `fin_transfer` with non-Near recipient` so that `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain` redirects principal or fee to a relayer under conditions that do not match the original user transfer, violating `foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain`
- Entrypoint: `public cross-chain forward path created by `fin_transfer` with non-Near recipient`
- Attacker controls: origin token address, target chain, fee split, and fast-transfer status
- Exploit idea: Target branches where a stored fast-transfer status replaces the canonical recipient or fee recipient.
- Invariant to test: foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Verify that relayer substitution happens only for the exact matching transfer id and exact matching parameters of the relayed fast payout.
