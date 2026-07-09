# Q3922: NEAR omni-types address/string utilities low-half deploy salt aliases another token id via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public transfer, proof, and token-mapping flows through every chain adapter` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs`` ends up accepting two inconsistent interpretations of the same economic event specifically around `low-half deploy salt aliases another token id` under converts user-controlled recipient strings and proof-derived addresses into typed `OmniAddress` values used throughout the bridge, violating `cross-chain address normalization must never let two textual encodings collapse onto one asset identity or one textual recipient resolve to the wrong chain/account`?

## Target
- File/function: `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs``
- Entrypoint: `public transfer, proof, and token-mapping flows through every chain adapter`
- Attacker controls: address strings, hex encodings, account ids, chain-kind tags, and recipient parsing across chains
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: cross-chain address normalization must never let two textual encodings collapse onto one asset identity or one textual recipient resolve to the wrong chain/account
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
