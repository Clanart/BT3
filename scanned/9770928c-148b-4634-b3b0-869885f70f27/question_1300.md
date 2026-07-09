# Q1300: Starknet old/new metadata ABI detection native versus wrapped registration confusion at boundary values

## Question
Can an unprivileged attacker trigger `public Starknet `log_metadata`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching` violate `ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity` in the `native versus wrapped registration confusion` attack class because switches between old-style felt252 metadata and new-style ByteArray metadata based on return-data length from untrusted token contracts becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching`
- Entrypoint: `public Starknet `log_metadata``
- Attacker controls: token contract that can return `name`/`symbol` as one felt, many felts, or malformed ABI-encoded data
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
