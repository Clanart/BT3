# Q2415: EVM bridge-token proxy deploy rollback fake bridge-controlled token accepted as canonical through cross-module drift

## Question
Can an unprivileged attacker use `public EVM `deployToken`` with control over token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `fake bridge-controlled token accepted as canonical` attack class because creates a proxy, emits events, and only then writes mapping state for the new bridge token, violating `deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes`
- Entrypoint: `public EVM `deployToken``
- Attacker controls: token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` and the adjacent token-mapping and asset-identity logic after every branch.
