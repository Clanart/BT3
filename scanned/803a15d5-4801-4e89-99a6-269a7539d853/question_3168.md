# Q3168: NEAR cross-chain token-address parsing hashed or padded seed collision at boundary values

## Question
Can an unprivileged attacker trigger `public deploy/finalize/proof paths` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types token/address parsing via proofs and deployment flows` violate `token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket` in the `hashed or padded seed collision` attack class because token identity is reconstructed from chain-specific address encodings before mapping to Near token ids becomes fragile at those edges?

## Target
- File/function: `near/omni-types token/address parsing via proofs and deployment flows`
- Entrypoint: `public deploy/finalize/proof paths`
- Attacker controls: foreign token addresses, chain tags, and textual or binary address forms
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
