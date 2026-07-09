# Q2228: NEAR omni-types address/string utilities hashed or padded seed collision via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public transfer, proof, and token-mapping flows through every chain adapter` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs`` ends up accepting two inconsistent interpretations of the same economic event specifically around `hashed or padded seed collision` under converts user-controlled recipient strings and proof-derived addresses into typed `OmniAddress` values used throughout the bridge, violating `cross-chain address normalization must never let two textual encodings collapse onto one asset identity or one textual recipient resolve to the wrong chain/account`?

## Target
- File/function: `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs``
- Entrypoint: `public transfer, proof, and token-mapping flows through every chain adapter`
- Attacker controls: address strings, hex encodings, account ids, chain-kind tags, and recipient parsing across chains
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: cross-chain address normalization must never let two textual encodings collapse onto one asset identity or one textual recipient resolve to the wrong chain/account
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
