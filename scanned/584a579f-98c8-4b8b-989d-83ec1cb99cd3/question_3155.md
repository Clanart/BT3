# Q3155: EVM bridge-token proxy deploy rollback low-half deploy salt aliases another token id at boundary values

## Question
Can an unprivileged attacker trigger `public EVM `deployToken`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` violate `deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa` in the `low-half deploy salt aliases another token id` attack class because creates a proxy, emits events, and only then writes mapping state for the new bridge token becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes`
- Entrypoint: `public EVM `deployToken``
- Attacker controls: token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
