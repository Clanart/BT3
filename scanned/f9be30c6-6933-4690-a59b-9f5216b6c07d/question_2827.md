# Q2827: NEAR omni-types address/string utilities address alias collapses distinct bridge subjects via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public transfer, proof, and token-mapping flows through every chain adapter` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs`` ends up accepting two inconsistent interpretations of the same economic event specifically around `address alias collapses distinct bridge subjects` under converts user-controlled recipient strings and proof-derived addresses into typed `OmniAddress` values used throughout the bridge, violating `cross-chain address normalization must never let two textual encodings collapse onto one asset identity or one textual recipient resolve to the wrong chain/account`?

## Target
- File/function: `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs``
- Entrypoint: `public transfer, proof, and token-mapping flows through every chain adapter`
- Attacker controls: address strings, hex encodings, account ids, chain-kind tags, and recipient parsing across chains
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: cross-chain address normalization must never let two textual encodings collapse onto one asset identity or one textual recipient resolve to the wrong chain/account
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
