# Q2369: Starknet init_transfer fee and principal split divergence through cross-module drift

## Question
Can an unprivileged attacker use `public Starknet outbound transfer entrypoint` with control over token address, amount, fee, native fee, recipient `ByteArray`, message `ByteArray`, and caller and desynchronize `starknet/src/omni_bridge.cairo::init_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fee and principal split divergence` attack class because checks pause flags, increments the origin nonce, burns bridge tokens when required, and emits an `InitTransfer` event using a normalized token address, violating `one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::init_transfer`
- Entrypoint: `public Starknet outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient `ByteArray`, message `ByteArray`, and caller
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::init_transfer` and the adjacent replay-protection bookkeeping after every branch.
