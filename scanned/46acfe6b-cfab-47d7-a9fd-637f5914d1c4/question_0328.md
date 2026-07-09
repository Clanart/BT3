# Q328: NEAR cross-chain token-address parsing asset-branch confusion on finalization via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public deploy/finalize/proof paths` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-types token/address parsing via proofs and deployment flows` ends up accepting two inconsistent interpretations of the same economic event specifically around `asset-branch confusion on finalization` under token identity is reconstructed from chain-specific address encodings before mapping to Near token ids, violating `token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket`?

## Target
- File/function: `near/omni-types token/address parsing via proofs and deployment flows`
- Entrypoint: `public deploy/finalize/proof paths`
- Attacker controls: foreign token addresses, chain tags, and textual or binary address forms
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
