# Q496: NEAR cross-chain token-address parsing asset-branch confusion on finalization through cross-module drift

## Question
Can an unprivileged attacker use `public deploy/finalize/proof paths` with control over foreign token addresses, chain tags, and textual or binary address forms and desynchronize `near/omni-types token/address parsing via proofs and deployment flows` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `asset-branch confusion on finalization` attack class because token identity is reconstructed from chain-specific address encodings before mapping to Near token ids, violating `token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket`?

## Target
- File/function: `near/omni-types token/address parsing via proofs and deployment flows`
- Entrypoint: `public deploy/finalize/proof paths`
- Attacker controls: foreign token addresses, chain tags, and textual or binary address forms
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Also assert cross-module consistency between `near/omni-types token/address parsing via proofs and deployment flows` and the adjacent token-mapping and asset-identity logic after every branch.
