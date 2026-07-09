# Q3303: NEAR cross-chain token-address parsing address alias collapses distinct bridge subjects

## Question
Can an unprivileged attacker exploit `public deploy/finalize/proof paths` so that `near/omni-types token/address parsing via proofs and deployment flows` normalizes two distinct chain-specific addresses into the same local representation, violating `token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket`?

## Target
- File/function: `near/omni-types token/address parsing via proofs and deployment flows`
- Entrypoint: `public deploy/finalize/proof paths`
- Attacker controls: foreign token addresses, chain tags, and textual or binary address forms
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters.
- Invariant to test: token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities.
