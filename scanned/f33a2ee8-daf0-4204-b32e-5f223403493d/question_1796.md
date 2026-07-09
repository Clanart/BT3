# Q1796: EVM bridge-token proxy deploy rollback native versus wrapped registration confusion through cross-module drift

## Question
Can an unprivileged attacker use `public EVM `deployToken`` with control over token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped registration confusion` attack class because creates a proxy, emits events, and only then writes mapping state for the new bridge token, violating `deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes`
- Entrypoint: `public EVM `deployToken``
- Attacker controls: token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` and the adjacent token-mapping and asset-identity logic after every branch.
