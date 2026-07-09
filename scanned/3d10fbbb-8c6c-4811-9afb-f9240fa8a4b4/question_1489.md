# Q1489: NEAR cross-chain token-address parsing native versus wrapped registration confusion

## Question
Can an unprivileged attacker reach `public deploy/finalize/proof paths` and make `near/omni-types token/address parsing via proofs and deployment flows` treat a wrapped asset as native or a native asset as wrapped because of token identity is reconstructed from chain-specific address encodings before mapping to Near token ids, violating `token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket`?

## Target
- File/function: `near/omni-types token/address parsing via proofs and deployment flows`
- Entrypoint: `public deploy/finalize/proof paths`
- Attacker controls: foreign token addresses, chain tags, and textual or binary address forms
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration.
- Invariant to test: token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model.
