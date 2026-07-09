# Q3960: NEAR cross-chain token-address parsing truncated seed or salt aliases remote assets via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public deploy/finalize/proof paths` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-types token/address parsing via proofs and deployment flows` ends up accepting two inconsistent interpretations of the same economic event specifically around `truncated seed or salt aliases remote assets` under token identity is reconstructed from chain-specific address encodings before mapping to Near token ids, violating `token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket`?

## Target
- File/function: `near/omni-types token/address parsing via proofs and deployment flows`
- Entrypoint: `public deploy/finalize/proof paths`
- Attacker controls: foreign token addresses, chain tags, and textual or binary address forms
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
