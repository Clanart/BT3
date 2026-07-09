# Q2580: NEAR cross-chain token-address parsing fake bridge-controlled token accepted as canonical at boundary values

## Question
Can an unprivileged attacker trigger `public deploy/finalize/proof paths` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types token/address parsing via proofs and deployment flows` violate `token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket` in the `fake bridge-controlled token accepted as canonical` attack class because token identity is reconstructed from chain-specific address encodings before mapping to Near token ids becomes fragile at those edges?

## Target
- File/function: `near/omni-types token/address parsing via proofs and deployment flows`
- Entrypoint: `public deploy/finalize/proof paths`
- Attacker controls: foreign token addresses, chain tags, and textual or binary address forms
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
