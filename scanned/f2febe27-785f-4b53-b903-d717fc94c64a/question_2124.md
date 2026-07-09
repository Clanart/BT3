# Q2124: NEAR cross-chain token-address parsing fake bridge-controlled token accepted as canonical

## Question
Can an unprivileged attacker use `public deploy/finalize/proof paths` to register or settle against a token that only looks bridge-controlled because `near/omni-types token/address parsing via proofs and deployment flows` relies on token identity is reconstructed from chain-specific address encodings before mapping to Near token ids, violating `token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket`?

## Target
- File/function: `near/omni-types token/address parsing via proofs and deployment flows`
- Entrypoint: `public deploy/finalize/proof paths`
- Attacker controls: foreign token addresses, chain tags, and textual or binary address forms
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity.
- Invariant to test: token-address parsing must not admit aliases that let one foreign asset reuse another asset’s Near token mapping or lock bucket
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset.
