# Q635: Starknet old/new metadata ABI detection malicious metadata manufactures a bridge identity at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet `log_metadata`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching` violate `ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity` in the `malicious metadata manufactures a bridge identity` attack class because switches between old-style felt252 metadata and new-style ByteArray metadata based on return-data length from untrusted token contracts becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching`
- Entrypoint: `public Starknet `log_metadata``
- Attacker controls: token contract that can return `name`/`symbol` as one felt, many felts, or malformed ABI-encoded data
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
