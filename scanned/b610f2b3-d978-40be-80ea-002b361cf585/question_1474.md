# Q1474: EVM bridge-token proxy deploy rollback native versus wrapped registration confusion

## Question
Can an unprivileged attacker reach `public EVM `deployToken`` and make `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` treat a wrapped asset as native or a native asset as wrapped because of creates a proxy, emits events, and only then writes mapping state for the new bridge token, violating `deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes`
- Entrypoint: `public EVM `deployToken``
- Attacker controls: token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration.
- Invariant to test: deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model.
