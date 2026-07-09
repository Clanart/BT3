# Q1101: Starknet BridgeToken burn native versus wrapped branch switch through cross-module drift

## Question
Can an unprivileged attacker use `public outbound-side burn path reached from `init_transfer`` with control over caller address and amount and desynchronize `starknet/src/bridge_token.cairo::burn` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped branch switch` attack class because burns wrapped supply from the caller before the bridge emits an outbound transfer event, violating `a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches`?

## Target
- File/function: `starknet/src/bridge_token.cairo::burn`
- Entrypoint: `public outbound-side burn path reached from `init_transfer``
- Attacker controls: caller address and amount
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: a burned Starknet balance must map one-to-one to one outbound bridge claim and must not be reusable or partially refunded through alternate branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Also assert cross-module consistency between `starknet/src/bridge_token.cairo::burn` and the adjacent mint, burn, or custody accounting after every branch.
