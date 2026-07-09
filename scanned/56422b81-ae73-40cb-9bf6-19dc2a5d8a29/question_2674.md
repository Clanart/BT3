# Q2674: Starknet BridgeToken burn global asset-conservation invariant break

## Question
Can an unprivileged attacker combine the public surface behind `public outbound-side burn path reached from `init_transfer`` with the code paths summarized by `starknet/src/bridge_token.cairo::burn` and make total redeemable claims across chains exceed the total burned, locked, or custodied assets tracked by burns wrapped supply from the caller before the bridge emits an outbound transfer event, violating `a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches`?

## Target
- File/function: `starknet/src/bridge_token.cairo::burn`
- Entrypoint: `public outbound-side burn path reached from `init_transfer``
- Attacker controls: caller address and amount
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class.
- Invariant to test: a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step.
