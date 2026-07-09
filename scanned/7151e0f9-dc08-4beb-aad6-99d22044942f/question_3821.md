# Q3821: Starknet bridge-token upgrade coupling burn debits the wrong logical account

## Question
Can an unprivileged attacker use `public bridge activity that depends on upgraded token implementations` so that `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade` burns or withholds value from a caller context different from the one the bridge event later attributes, violating `public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::upgrade_token plus bridge_token.cairo::upgrade`
- Entrypoint: `public bridge activity that depends on upgraded token implementations`
- Attacker controls: token address chosen from the bridge map and any transfer state that spans pre/post upgrade token classes
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject.
- Invariant to test: public bridge flows must not become exploitable through an implementation mismatch that changes supply semantics for already-mapped tokens
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event.
