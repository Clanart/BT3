# Q3789: Starknet init_transfer native fee and token fee drawn from wrong asset bucket

## Question
Can an unprivileged attacker use `public Starknet outbound transfer entrypoint` to make `starknet/src/omni_bridge.cairo::init_transfer` pay native fee and token fee from inconsistent custody pools because of checks pause flags, increments the origin nonce, burns bridge tokens when required, and emits an `InitTransfer` event using a normalized token address, violating `one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::init_transfer`
- Entrypoint: `public Starknet outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient `ByteArray`, message `ByteArray`, and caller
- Exploit idea: Focus on branches that mint native-fee tokens, transfer escrowed tokens, or unwrap wrapped native assets.
- Invariant to test: one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains
- Expected Immunefi impact: Balance manipulation
- Fast validation: Trace fee asset origin across every branch and assert that each fee component comes from the asset pool the bridge actually consumed.
