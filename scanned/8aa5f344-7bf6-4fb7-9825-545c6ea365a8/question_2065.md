# Q2065: Starknet init_transfer fee and principal split divergence

## Question
Can an unprivileged attacker enter through `public Starknet outbound transfer entrypoint` with crafted amount, fee, or native-fee inputs and make `starknet/src/omni_bridge.cairo::init_transfer` use inconsistent fee and principal values across checks pause flags, increments the origin nonce, burns bridge tokens when required, and emits an `InitTransfer` event using a normalized token address, violating `one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::init_transfer`
- Entrypoint: `public Starknet outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient `ByteArray`, message `ByteArray`, and caller
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing.
- Invariant to test: one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value.
