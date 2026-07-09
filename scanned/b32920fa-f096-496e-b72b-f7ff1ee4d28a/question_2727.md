# Q2727: NEAR cross-chain token-address parsing hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public deploy/finalize/proof paths` with overlong or adversarial token identifiers and make `near/omni-types token/address parsing via proofs and deployment flows` derive the same local seed or salt for two remote assets because of token identity is reconstructed from chain-specific address encodings before mapping to Near token ids, violating `token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket`?

## Target
- File/function: `near/omni-types token/address parsing via proofs and deployment flows`
- Entrypoint: `public deploy/finalize/proof paths`
- Attacker controls: foreign token addresses, chain tags, and textual or binary address forms
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
