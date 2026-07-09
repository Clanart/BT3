# Q2521: Starknet init_transfer fee and principal split divergence at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet outbound transfer entrypoint` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `starknet/src/omni_bridge.cairo::init_transfer` violate `one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains` in the `fee and principal split divergence` attack class because checks pause flags, increments the origin nonce, burns bridge tokens when required, and emits an `InitTransfer` event using a normalized token address becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::init_transfer`
- Entrypoint: `public Starknet outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient `ByteArray`, message `ByteArray`, and caller
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
