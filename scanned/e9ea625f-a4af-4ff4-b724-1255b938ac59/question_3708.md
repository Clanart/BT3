# Q3708: NEAR cross-chain token-address parsing address alias collapses distinct bridge subjects at boundary values

## Question
Can an unprivileged attacker trigger `public deploy/finalize/proof paths` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types token/address parsing via proofs and deployment flows` violate `token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket` in the `address alias collapses distinct bridge subjects` attack class because token identity is reconstructed from chain-specific address encodings before mapping to Near token ids becomes fragile at those edges?

## Target
- File/function: `near/omni-types token/address parsing via proofs and deployment flows`
- Entrypoint: `public deploy/finalize/proof paths`
- Attacker controls: foreign token addresses, chain tags, and textual or binary address forms
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
