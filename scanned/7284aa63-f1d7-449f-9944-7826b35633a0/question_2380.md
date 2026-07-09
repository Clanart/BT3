# Q2380: NEAR omni-types address/string utilities hashed or padded seed collision through cross-module drift

## Question
Can an unprivileged attacker use `public transfer, proof, and token-mapping flows through every chain adapter` with control over address strings, hex encodings, account ids, chain-kind tags, and recipient parsing across chains and desynchronize `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs`` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `hashed or padded seed collision` attack class because converts user-controlled recipient strings and proof-derived addresses into typed `OmniAddress` values used throughout the bridge, violating `cross-chain address normalization must never let two textual encodings collapse onto one asset identity or one textual recipient resolve to the wrong chain/account`?

## Target
- File/function: `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs``
- Entrypoint: `public transfer, proof, and token-mapping flows through every chain adapter`
- Attacker controls: address strings, hex encodings, account ids, chain-kind tags, and recipient parsing across chains
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: cross-chain address normalization must never let two textual encodings collapse onto one asset identity or one textual recipient resolve to the wrong chain/account
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity. Also assert cross-module consistency between `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs`` and the adjacent token-mapping and asset-identity logic after every branch.
