# Q3021: NEAR cross-chain token-address parsing hashed or padded seed collision through cross-module drift

## Question
Can an unprivileged attacker use `public deploy/finalize/proof paths` with control over foreign token addresses, chain tags, and textual or binary address forms and desynchronize `near/omni-types token/address parsing via proofs and deployment flows` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `hashed or padded seed collision` attack class because token identity is reconstructed from chain-specific address encodings before mapping to Near token ids, violating `token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket`?

## Target
- File/function: `near/omni-types token/address parsing via proofs and deployment flows`
- Entrypoint: `public deploy/finalize/proof paths`
- Attacker controls: foreign token addresses, chain tags, and textual or binary address forms
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity. Also assert cross-module consistency between `near/omni-types token/address parsing via proofs and deployment flows` and the adjacent token-mapping and asset-identity logic after every branch.
