# Q1267: Starknet BridgeToken burn native versus wrapped branch switch at boundary values

## Question
Can an unprivileged attacker trigger `public outbound-side burn path reached from `init_transfer`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `starknet/src/bridge_token.cairo::burn` violate `a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches` in the `native versus wrapped branch switch` attack class because burns wrapped supply from the caller before the bridge emits an outbound transfer event becomes fragile at those edges?

## Target
- File/function: `starknet/src/bridge_token.cairo::burn`
- Entrypoint: `public outbound-side burn path reached from `init_transfer``
- Attacker controls: caller address and amount
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
