# Q1908: Starknet verify_borsh_signature missing chain or contract domain separation at boundary values

## Question
Can an unprivileged attacker trigger `public signature-check path through `deploy_token` and `fin_transfer`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `starknet/src/omni_bridge.cairo::_verify_borsh_signature` violate `signature verification must be domain-separated and malleability-safe so one signature cannot authorize multiple Starknet bridge actions` in the `missing chain or contract domain separation` attack class because hashes Borsh bytes with Keccak, reconstructs an Ethereum-style signature, and checks it against the configured derived address becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_verify_borsh_signature`
- Entrypoint: `public signature-check path through `deploy_token` and `fin_transfer``
- Attacker controls: serialized Borsh payload bytes, signature `v/r/s`, and configured derived address
- Exploit idea: Target validators keyed by derived signer, block hash, emitter address, or payload bytes that omit some domain field. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: signature verification must be domain-separated and malleability-safe so one signature cannot authorize multiple Starknet bridge actions
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt cross-chain and cross-contract replay of the same validated bytes and assert that every trust domain field participates in acceptance. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
