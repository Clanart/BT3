# Q2965: Starknet init_transfer native versus wrapped branch switch through cross-module drift

## Question
Can an unprivileged attacker use `public Starknet outbound transfer entrypoint` with control over token address, amount, fee, native fee, recipient `ByteArray`, message `ByteArray`, and caller and desynchronize `starknet/src/omni_bridge.cairo::init_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped branch switch` attack class because checks pause flags, increments the origin nonce, burns bridge tokens when required, and emits an `InitTransfer` event using a normalized token address, violating `one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::init_transfer`
- Entrypoint: `public Starknet outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient `ByteArray`, message `ByteArray`, and caller
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::init_transfer` and the adjacent replay-protection bookkeeping after every branch.
