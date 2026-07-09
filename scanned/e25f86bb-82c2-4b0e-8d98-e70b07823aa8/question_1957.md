# Q1957: EVM bridge-token proxy deploy rollback native versus wrapped registration confusion at boundary values

## Question
Can an unprivileged attacker trigger `public EVM `deployToken`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` violate `deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa` in the `native versus wrapped registration confusion` attack class because creates a proxy, emits events, and only then writes mapping state for the new bridge token becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes`
- Entrypoint: `public EVM `deployToken``
- Attacker controls: token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
