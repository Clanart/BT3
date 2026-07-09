# Q769: Starknet BridgeToken burn native versus wrapped branch switch

## Question
Can an unprivileged attacker choose inputs to `public outbound-side burn path reached from `init_transfer`` that make `starknet/src/bridge_token.cairo::burn` classify the asset differently before and after a custody-changing step through burns wrapped supply from the caller before the bridge emits an outbound transfer event, violating `a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches`?

## Target
- File/function: `starknet/src/bridge_token.cairo::burn`
- Entrypoint: `public outbound-side burn path reached from `init_transfer``
- Attacker controls: caller address and amount
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates.
- Invariant to test: a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models.
