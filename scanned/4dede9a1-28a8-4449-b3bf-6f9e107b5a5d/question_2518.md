# Q2518: Starknet log_metadata hashed or padded seed collision at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet metadata logging entrypoint` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `starknet/src/omni_bridge.cairo::log_metadata` violate `metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains` in the `hashed or padded seed collision` attack class because performs low-level calls to infer whether metadata fields are returned as `felt252` or `ByteArray`, then emits a `LogMetadata` event becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata`
- Entrypoint: `public Starknet metadata logging entrypoint`
- Attacker controls: token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
