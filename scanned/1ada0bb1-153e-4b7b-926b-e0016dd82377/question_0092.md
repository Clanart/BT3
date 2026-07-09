# Q92: Starknet init_transfer origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through `public Starknet outbound transfer entrypoint` with control over token address, amount, fee, native fee, recipient `ByteArray`, message `ByteArray`, and caller and make `starknet/src/omni_bridge.cairo::init_transfer` advance or reuse bridge nonces inconsistently with checks pause flags, increments the origin nonce, burns bridge tokens when required, and emits an `InitTransfer` event using a normalized token address, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::init_transfer`
- Entrypoint: `public Starknet outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient `ByteArray`, message `ByteArray`, and caller
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
