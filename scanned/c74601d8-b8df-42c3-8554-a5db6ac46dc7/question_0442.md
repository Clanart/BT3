# Q442: NEAR omni-types address/string utilities recipient or message ambiguity through cross-module drift

## Question
Can an unprivileged attacker use `public transfer, proof, and token-mapping flows through every chain adapter` with control over address strings, hex encodings, account ids, chain-kind tags, and recipient parsing across chains and desynchronize `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs`` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or message ambiguity` attack class because converts user-controlled recipient strings and proof-derived addresses into typed `OmniAddress` values used throughout the bridge, violating `cross-chain address normalization must never let two textual encodings collapse onto one asset identity or one textual recipient resolve to the wrong chain/account`?

## Target
- File/function: `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs``
- Entrypoint: `public transfer, proof, and token-mapping flows through every chain adapter`
- Attacker controls: address strings, hex encodings, account ids, chain-kind tags, and recipient parsing across chains
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: cross-chain address normalization must never let two textual encodings collapse onto one asset identity or one textual recipient resolve to the wrong chain/account
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Also assert cross-module consistency between `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs`` and the adjacent token-mapping and asset-identity logic after every branch.
