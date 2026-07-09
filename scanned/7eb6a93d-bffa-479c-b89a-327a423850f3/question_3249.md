# Q3249: Starknet log_metadata ABI version switch changes metadata identity

## Question
Can an unprivileged attacker trigger `public Starknet metadata logging entrypoint` so that `starknet/src/omni_bridge.cairo::log_metadata` chooses the wrong ABI branch for metadata parsing because of performs low-level calls to infer whether metadata fields are returned as `felt252` or `ByteArray`, then emits a `LogMetadata` event, violating `metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata`
- Entrypoint: `public Starknet metadata logging entrypoint`
- Attacker controls: token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior
- Exploit idea: Target old-style versus new-style token metadata return shapes and zero-length special cases.
- Invariant to test: metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Return ambiguous ABI payloads and assert that the bridge either rejects them or derives the exact intended metadata once.
