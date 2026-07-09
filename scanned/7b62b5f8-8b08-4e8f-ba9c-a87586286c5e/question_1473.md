# Q1473: Starknet bridge-token upgrade coupling asset-branch confusion on finalization

## Question
Can an unprivileged attacker use `public bridge activity that depends on upgraded token implementations` to make `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade` release value through a more favorable branch than the source event actually authorized because of bridge token upgrades are admin-gated, but public settlement and outbound flows depend on the assumption that token logic before and after upgrade preserves burn/mint invariants, violating `public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade`
- Entrypoint: `public bridge activity that depends on upgraded token implementations`
- Attacker controls: token address chosen from the bridge map and any transfer state that spans pre/post upgrade token classes
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches.
- Invariant to test: public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state.
