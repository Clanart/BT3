# Q3112: Starknet init_transfer native versus wrapped branch switch at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet outbound transfer entrypoint` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `starknet/src/omni_bridge.cairo::init_transfer` violate `one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains` in the `native versus wrapped branch switch` attack class because checks pause flags, increments the origin nonce, burns bridge tokens when required, and emits an `InitTransfer` event using a normalized token address becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::init_transfer`
- Entrypoint: `public Starknet outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient `ByteArray`, message `ByteArray`, and caller
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
