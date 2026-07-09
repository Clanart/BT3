# Q3519: Starknet log_metadata ABI version switch changes metadata identity through cross-module drift

## Question
Can an unprivileged attacker use `public Starknet metadata logging entrypoint` with control over token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior and desynchronize `starknet/src/omni_bridge.cairo::log_metadata` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `ABI version switch changes metadata identity` attack class because performs low-level calls to infer whether metadata fields are returned as `felt252` or `ByteArray`, then emits a `LogMetadata` event, violating `metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata`
- Entrypoint: `public Starknet metadata logging entrypoint`
- Attacker controls: token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior
- Exploit idea: Target old-style versus new-style token metadata return shapes and zero-length special cases. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Return ambiguous ABI payloads and assert that the bridge either rejects them or derives the exact intended metadata once. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::log_metadata` and the adjacent mint, burn, or custody accounting after every branch.
