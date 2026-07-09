# Q2101: Starknet old/new metadata ABI detection hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public Starknet `log_metadata`` with overlong or adversarial token identifiers and make `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching` derive the same local seed or salt for two remote assets because of switches between old-style felt252 metadata and new-style ByteArray metadata based on return-data length from untrusted token contracts, violating `ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching`
- Entrypoint: `public Starknet `log_metadata``
- Attacker controls: token contract that can return `name`/`symbol` as one felt, many felts, or malformed ABI-encoded data
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
