# Q602: Starknet BridgeToken burn burn or lock before irreversible state at boundary values

## Question
Can an unprivileged attacker trigger `public outbound-side burn path reached from `init_transfer`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `starknet/src/bridge_token.cairo::burn` violate `a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches` in the `burn or lock before irreversible state` attack class because burns wrapped supply from the caller before the bridge emits an outbound transfer event becomes fragile at those edges?

## Target
- File/function: `starknet/src/bridge_token.cairo::burn`
- Entrypoint: `public outbound-side burn path reached from `init_transfer``
- Attacker controls: caller address and amount
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
