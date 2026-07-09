# Q3560: EVM bridge-token proxy deploy rollback mint-with-message path differs economically from plain mint through cross-module drift

## Question
Can an unprivileged attacker use `public EVM `deployToken`` with control over token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `mint-with-message path differs economically from plain mint` attack class because creates a proxy, emits events, and only then writes mapping state for the new bridge token, violating `deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes`
- Entrypoint: `public EVM `deployToken``
- Attacker controls: token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` and the adjacent token-mapping and asset-identity logic after every branch.
