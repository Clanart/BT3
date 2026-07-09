# Q2524: Starknet BridgeToken burn custody accounting diverges from wrapped supply at boundary values

## Question
Can an unprivileged attacker trigger `public outbound-side burn path reached from `init_transfer`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `starknet/src/bridge_token.cairo::burn` violate `a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches` in the `custody accounting diverges from wrapped supply` attack class because burns wrapped supply from the caller before the bridge emits an outbound transfer event becomes fragile at those edges?

## Target
- File/function: `starknet/src/bridge_token.cairo::burn`
- Entrypoint: `public outbound-side burn path reached from `init_transfer``
- Attacker controls: caller address and amount
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
