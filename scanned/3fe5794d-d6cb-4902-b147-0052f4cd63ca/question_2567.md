# Q2567: EVM bridge-token proxy deploy rollback fake bridge-controlled token accepted as canonical at boundary values

## Question
Can an unprivileged attacker trigger `public EVM `deployToken`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` violate `deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa` in the `fake bridge-controlled token accepted as canonical` attack class because creates a proxy, emits events, and only then writes mapping state for the new bridge token becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes`
- Entrypoint: `public EVM `deployToken``
- Attacker controls: token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
