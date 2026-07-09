# Q3654: Starknet log_metadata ABI version switch changes metadata identity at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet metadata logging entrypoint` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `starknet/src/omni_bridge.cairo::log_metadata` violate `metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains` in the `ABI version switch changes metadata identity` attack class because performs low-level calls to infer whether metadata fields are returned as `felt252` or `ByteArray`, then emits a `LogMetadata` event becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata`
- Entrypoint: `public Starknet metadata logging entrypoint`
- Attacker controls: token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior
- Exploit idea: Target old-style versus new-style token metadata return shapes and zero-length special cases. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Return ambiguous ABI payloads and assert that the bridge either rejects them or derives the exact intended metadata once. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
