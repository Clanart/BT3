# Q3115: Starknet BridgeToken burn global asset-conservation invariant break at boundary values

## Question
Can an unprivileged attacker trigger `public outbound-side burn path reached from `init_transfer`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `starknet/src/bridge_token.cairo::burn` violate `a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches` in the `global asset-conservation invariant break` attack class because burns wrapped supply from the caller before the bridge emits an outbound transfer event becomes fragile at those edges?

## Target
- File/function: `starknet/src/bridge_token.cairo::burn`
- Entrypoint: `public outbound-side burn path reached from `init_transfer``
- Attacker controls: caller address and amount
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
