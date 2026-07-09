# Q3008: EVM bridge-token proxy deploy rollback low-half deploy salt aliases another token id through cross-module drift

## Question
Can an unprivileged attacker use `public EVM `deployToken`` with control over token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `low-half deploy salt aliases another token id` attack class because creates a proxy, emits events, and only then writes mapping state for the new bridge token, violating `deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes`
- Entrypoint: `public EVM `deployToken``
- Attacker controls: token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` and the adjacent token-mapping and asset-identity logic after every branch.
