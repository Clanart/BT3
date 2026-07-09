# Q664: NEAR cross-chain token-address parsing asset-branch confusion on finalization at boundary values

## Question
Can an unprivileged attacker trigger `public deploy/finalize/proof paths` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types token/address parsing via proofs and deployment flows` violate `token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket` in the `asset-branch confusion on finalization` attack class because token identity is reconstructed from chain-specific address encodings before mapping to Near token ids becomes fragile at those edges?

## Target
- File/function: `near/omni-types token/address parsing via proofs and deployment flows`
- Entrypoint: `public deploy/finalize/proof paths`
- Attacker controls: foreign token addresses, chain tags, and textual or binary address forms
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
