# Q1328: NEAR cross-chain token-address parsing canonical token identity collision at boundary values

## Question
Can an unprivileged attacker trigger `public deploy/finalize/proof paths` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types token/address parsing via proofs and deployment flows` violate `token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket` in the `canonical token identity collision` attack class because token identity is reconstructed from chain-specific address encodings before mapping to Near token ids becomes fragile at those edges?

## Target
- File/function: `near/omni-types token/address parsing via proofs and deployment flows`
- Entrypoint: `public deploy/finalize/proof paths`
- Attacker controls: foreign token addresses, chain tags, and textual or binary address forms
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
