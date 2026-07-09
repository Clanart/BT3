# Q3010: NEAR add_token mapping writer low-half deploy salt aliases another token id through cross-module drift

## Question
Can an unprivileged attacker use `public deploy/bind flows through internal mapping writes` with control over token id, foreign token address, decimals, and origin decimals and desynchronize `near/omni-bridge/src/lib.rs::add_token` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `low-half deploy salt aliases another token id` attack class because writes the core `token_id_to_address`, `token_address_to_id`, and `token_decimals` state that every bridge path later trusts, violating `mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_token`
- Entrypoint: `public deploy/bind flows through internal mapping writes`
- Attacker controls: token id, foreign token address, decimals, and origin decimals
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::add_token` and the adjacent token-mapping and asset-identity logic after every branch.
