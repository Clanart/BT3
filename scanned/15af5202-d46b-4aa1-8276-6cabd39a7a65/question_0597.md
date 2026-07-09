# Q597: Starknet verify_borsh_signature state update before full validation at boundary values

## Question
Can an unprivileged attacker trigger `public signature-check path through `deploy_token` and `fin_transfer`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `starknet/src/omni_bridge.cairo::_verify_borsh_signature` violate `signature verification must be domain-separated and malleability-safe so one signature cannot authorize multiple Starknet bridge actions` in the `state update before full validation` attack class because hashes Borsh bytes with Keccak, reconstructs an Ethereum-style signature, and checks it against the configured derived address becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_verify_borsh_signature`
- Entrypoint: `public signature-check path through `deploy_token` and `fin_transfer``
- Attacker controls: serialized Borsh payload bytes, signature `v/r/s`, and configured derived address
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: signature verification must be domain-separated and malleability-safe so one signature cannot authorize multiple Starknet bridge actions
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
