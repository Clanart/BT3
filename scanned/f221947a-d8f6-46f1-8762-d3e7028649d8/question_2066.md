# Q2066: Starknet verify_borsh_signature parser boundary or offset manipulation

## Question
Can an unprivileged attacker craft proof bytes for `public signature-check path through `deploy_token` and `fin_transfer`` that make `starknet/src/omni_bridge.cairo::_verify_borsh_signature` shift field boundaries, truncate payloads, or reinterpret trailing bytes because of hashes Borsh bytes with Keccak, reconstructs an Ethereum-style signature, and checks it against the configured derived address, violating `signature verification must be domain-separated and malleability-safe so one signature cannot authorize multiple Starknet bridge actions`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_verify_borsh_signature`
- Entrypoint: `public signature-check path through `deploy_token` and `fin_transfer``
- Attacker controls: serialized Borsh payload bytes, signature `v/r/s`, and configured derived address
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders.
- Invariant to test: signature verification must be domain-separated and malleability-safe so one signature cannot authorize multiple Starknet bridge actions
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields.
