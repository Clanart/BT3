# Q3948: EVM bridge-token proxy deploy rollback asset mapping drifts away from actual token semantics via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM `deployToken`` and then replay or reorder later callback or refund resolution so that `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` ends up accepting two inconsistent interpretations of the same economic event specifically around `asset mapping drifts away from actual token semantics` under creates a proxy, emits events, and only then writes mapping state for the new bridge token, violating `deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes`
- Entrypoint: `public EVM `deployToken``
- Attacker controls: token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes
- Exploit idea: Target upgrades, migration swaps, fake bridge-controlled tokens, and deploy callbacks. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Change token semantics around an existing mapping and assert that the bridge does not keep treating the token as a valid canonical representation. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
