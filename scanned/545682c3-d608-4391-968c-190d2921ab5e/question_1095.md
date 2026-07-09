# Q1095: Starknet init_transfer burn or lock before irreversible state through cross-module drift

## Question
Can an unprivileged attacker use `public Starknet outbound transfer entrypoint` with control over token address, amount, fee, native fee, recipient `ByteArray`, message `ByteArray`, and caller and desynchronize `starknet/src/omni_bridge.cairo::init_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `burn or lock before irreversible state` attack class because checks pause flags, increments the origin nonce, burns bridge tokens when required, and emits an `InitTransfer` event using a normalized token address, violating `one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::init_transfer`
- Entrypoint: `public Starknet outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient `ByteArray`, message `ByteArray`, and caller
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::init_transfer` and the adjacent replay-protection bookkeeping after every branch.
