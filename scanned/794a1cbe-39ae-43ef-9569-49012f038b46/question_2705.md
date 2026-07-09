# Q2705: Starknet old/new metadata ABI detection truncated seed or salt aliases remote assets

## Question
Can an unprivileged attacker reach `public Starknet `log_metadata`` and make `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching` truncate or hash remote asset identifiers in a way that aliases two deployable assets, violating `ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching`
- Entrypoint: `public Starknet `log_metadata``
- Attacker controls: token contract that can return `name`/`symbol` as one felt, many felts, or malformed ABI-encoded data
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers.
- Invariant to test: ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA.
