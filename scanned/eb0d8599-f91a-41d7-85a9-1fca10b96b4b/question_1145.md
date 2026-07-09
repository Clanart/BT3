# Q1145: Starknet bridge-token upgrade coupling native versus wrapped branch switch through cross-module drift

## Question
Can an unprivileged attacker use `public bridge activity that depends on upgraded token implementations` with control over token address chosen from the bridge map and any transfer state that spans pre/post upgrade token classes and desynchronize `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped branch switch` attack class because bridge token upgrades are admin-gated, but public settlement and outbound flows depend on the assumption that token logic before and after upgrade preserves burn/mint invariants, violating `public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade`
- Entrypoint: `public bridge activity that depends on upgraded token implementations`
- Attacker controls: token address chosen from the bridge map and any transfer state that spans pre/post upgrade token classes
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade` and the adjacent mint, burn, or custody accounting after every branch.
