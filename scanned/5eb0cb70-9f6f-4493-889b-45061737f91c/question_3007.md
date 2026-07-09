# Q3007: Starknet bridge-token upgrade coupling native versus wrapped registration confusion through cross-module drift

## Question
Can an unprivileged attacker use `public bridge activity that depends on upgraded token implementations` with control over token address chosen from the bridge map and any transfer state that spans pre/post upgrade token classes and desynchronize `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped registration confusion` attack class because bridge token upgrades are admin-gated, but public settlement and outbound flows depend on the assumption that token logic before and after upgrade preserves burn/mint invariants, violating `public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade`
- Entrypoint: `public bridge activity that depends on upgraded token implementations`
- Attacker controls: token address chosen from the bridge map and any transfer state that spans pre/post upgrade token classes
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade` and the adjacent mint, burn, or custody accounting after every branch.
