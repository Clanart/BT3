# Q434: Starknet BridgeToken burn burn or lock before irreversible state through cross-module drift

## Question
Can an unprivileged attacker use `public outbound-side burn path reached from `init_transfer`` with control over caller address and amount and desynchronize `starknet/src/bridge_token.cairo::burn` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `burn or lock before irreversible state` attack class because burns wrapped supply from the caller before the bridge emits an outbound transfer event, violating `a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches`?

## Target
- File/function: `starknet/src/bridge_token.cairo::burn`
- Entrypoint: `public outbound-side burn path reached from `init_transfer``
- Attacker controls: caller address and amount
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Also assert cross-module consistency between `starknet/src/bridge_token.cairo::burn` and the adjacent mint, burn, or custody accounting after every branch.
