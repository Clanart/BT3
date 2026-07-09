# Q3552: Starknet old/new metadata ABI detection ABI version switch changes metadata identity through cross-module drift

## Question
Can an unprivileged attacker use `public Starknet `log_metadata`` with control over token contract that can return `name`/`symbol` as one felt, many felts, or malformed ABI-encoded data and desynchronize `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `ABI version switch changes metadata identity` attack class because switches between old-style felt252 metadata and new-style ByteArray metadata based on return-data length from untrusted token contracts, violating `ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching`
- Entrypoint: `public Starknet `log_metadata``
- Attacker controls: token contract that can return `name`/`symbol` as one felt, many felts, or malformed ABI-encoded data
- Exploit idea: Target old-style versus new-style token metadata return shapes and zero-length special cases. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Return ambiguous ABI payloads and assert that the bridge either rejects them or derives the exact intended metadata once. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching` and the adjacent mint, burn, or custody accounting after every branch.
