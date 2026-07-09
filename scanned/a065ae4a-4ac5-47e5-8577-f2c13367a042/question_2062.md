# Q2062: Starknet log_metadata hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public Starknet metadata logging entrypoint` with overlong or adversarial token identifiers and make `starknet/src/omni_bridge.cairo::log_metadata` derive the same local seed or salt for two remote assets because of performs low-level calls to infer whether metadata fields are returned as `felt252` or `ByteArray`, then emits a `LogMetadata` event, violating `metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata`
- Entrypoint: `public Starknet metadata logging entrypoint`
- Attacker controls: token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
