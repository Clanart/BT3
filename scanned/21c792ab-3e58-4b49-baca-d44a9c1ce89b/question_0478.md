# Q478: Starknet bridge-token upgrade coupling burn or lock before irreversible state through cross-module drift

## Question
Can an unprivileged attacker use `public bridge activity that depends on upgraded token implementations` with control over token address chosen from the bridge map and any transfer state that spans pre/post upgrade token classes and desynchronize `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `burn or lock before irreversible state` attack class because bridge token upgrades are admin-gated, but public settlement and outbound flows depend on the assumption that token logic before and after upgrade preserves burn/mint invariants, violating `public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade`
- Entrypoint: `public bridge activity that depends on upgraded token implementations`
- Attacker controls: token address chosen from the bridge map and any transfer state that spans pre/post upgrade token classes
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade` and the adjacent mint, burn, or custody accounting after every branch.
