# Q646: Starknet bridge-token upgrade coupling burn or lock before irreversible state at boundary values

## Question
Can an unprivileged attacker trigger `public bridge activity that depends on upgraded token implementations` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade` violate `public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens` in the `burn or lock before irreversible state` attack class because bridge token upgrades are admin-gated, but public settlement and outbound flows depend on the assumption that token logic before and after upgrade preserves burn/mint invariants becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade`
- Entrypoint: `public bridge activity that depends on upgraded token implementations`
- Attacker controls: token address chosen from the bridge map and any transfer state that spans pre/post upgrade token classes
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
