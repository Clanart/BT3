# Q3425: EVM bridge-token proxy deploy rollback mint-with-message path differs economically from plain mint via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM `deployToken`` and then replay or reorder later callback or refund resolution so that `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` ends up accepting two inconsistent interpretations of the same economic event specifically around `mint-with-message path differs economically from plain mint` under creates a proxy, emits events, and only then writes mapping state for the new bridge token, violating `deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes`
- Entrypoint: `public EVM `deployToken``
- Attacker controls: token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
