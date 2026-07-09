# Q429: Starknet verify_borsh_signature state update before full validation through cross-module drift

## Question
Can an unprivileged attacker use `public signature-check path through `deploy_token` and `fin_transfer`` with control over serialized Borsh payload bytes, signature `v/r/s`, and configured derived address and desynchronize `starknet/src/omni_bridge.cairo::_verify_borsh_signature` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `state update before full validation` attack class because hashes Borsh bytes with Keccak, reconstructs an Ethereum-style signature, and checks it against the configured derived address, violating `signature verification must be domain-separated and malleability-safe so one signature cannot authorize multiple Starknet bridge actions`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_verify_borsh_signature`
- Entrypoint: `public signature-check path through `deploy_token` and `fin_transfer``
- Attacker controls: serialized Borsh payload bytes, signature `v/r/s`, and configured derived address
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: signature verification must be domain-separated and malleability-safe so one signature cannot authorize multiple Starknet bridge actions
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::_verify_borsh_signature` and the adjacent proof parsing and source authentication after every branch.
