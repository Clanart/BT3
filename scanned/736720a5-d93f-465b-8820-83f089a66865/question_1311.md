# Q1311: Starknet bridge-token upgrade coupling native versus wrapped branch switch at boundary values

## Question
Can an unprivileged attacker trigger `public bridge activity that depends on upgraded token implementations` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade` violate `public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens` in the `native versus wrapped branch switch` attack class because bridge token upgrades are admin-gated, but public settlement and outbound flows depend on the assumption that token logic before and after upgrade preserves burn/mint invariants becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade`
- Entrypoint: `public bridge activity that depends on upgraded token implementations`
- Attacker controls: token address chosen from the bridge map and any transfer state that spans pre/post upgrade token classes
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
