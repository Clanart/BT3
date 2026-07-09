# Q3002: NEAR foreign-to-foreign bridge forwarding relayer substitution changes economic recipient through cross-module drift

## Question
Can an unprivileged attacker use `public cross-chain forward path created by `fin_transfer` with non-Near recipient` with control over origin token address, target chain, fee split, and fast-transfer status and desynchronize `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `relayer substitution changes economic recipient` attack class because maps a foreign asset into its target-chain representation and either stores or fast-resolves a new pending transfer without ever landing value on Near, violating `foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain`
- Entrypoint: `public cross-chain forward path created by `fin_transfer` with non-Near recipient`
- Attacker controls: origin token address, target chain, fee split, and fast-transfer status
- Exploit idea: Target branches where a stored fast-transfer status replaces the canonical recipient or fee recipient. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Verify that relayer substitution happens only for the exact matching transfer id and exact matching parameters of the relayed fast payout. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain` and the adjacent token-mapping and asset-identity logic after every branch.
