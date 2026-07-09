# Q142: Starknet bridge-token upgrade coupling burn or lock before irreversible state

## Question
Can an unprivileged attacker use `public bridge activity that depends on upgraded token implementations` to force `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade` to burn or lock assets before the transfer record becomes safely irreversible, and then recover or redirect the bridge flow via bridge token upgrades are admin-gated, but public settlement and outbound flows depend on the assumption that token logic before and after upgrade preserves burn/mint invariants, violating `public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade`
- Entrypoint: `public bridge activity that depends on upgraded token implementations`
- Attacker controls: token address chosen from the bridge map and any transfer state that spans pre/post upgrade token classes
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed.
- Invariant to test: public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped.
