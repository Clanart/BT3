# Q2366: Starknet log_metadata hashed or padded seed collision through cross-module drift

## Question
Can an unprivileged attacker use `public Starknet metadata logging entrypoint` with control over token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior and desynchronize `starknet/src/omni_bridge.cairo::log_metadata` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `hashed or padded seed collision` attack class because performs low-level calls to infer whether metadata fields are returned as `felt252` or `ByteArray`, then emits a `LogMetadata` event, violating `metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata`
- Entrypoint: `public Starknet metadata logging entrypoint`
- Attacker controls: token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::log_metadata` and the adjacent mint, burn, or custody accounting after every branch.
