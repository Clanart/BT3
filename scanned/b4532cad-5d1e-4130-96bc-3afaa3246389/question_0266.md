# Q266: Starknet BridgeToken burn burn or lock before irreversible state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public outbound-side burn path reached from `init_transfer`` and then replay or reorder the later settlement leg on another chain so that `starknet/src/bridge_token.cairo::burn` ends up accepting two inconsistent interpretations of the same economic event specifically around `burn or lock before irreversible state` under burns wrapped supply from the caller before the bridge emits an outbound transfer event, violating `a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches`?

## Target
- File/function: `starknet/src/bridge_token.cairo::burn`
- Entrypoint: `public outbound-side burn path reached from `init_transfer``
- Attacker controls: caller address and amount
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
