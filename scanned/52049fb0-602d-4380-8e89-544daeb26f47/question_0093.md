# Q93: Starknet verify_borsh_signature state update before full validation

## Question
Can an unprivileged attacker exploit `public signature-check path through `deploy_token` and `fin_transfer`` so that `starknet/src/omni_bridge.cairo::_verify_borsh_signature` mutates finalization state before all signature or proof checks implied by hashes Borsh bytes with Keccak, reconstructs an Ethereum-style signature, and checks it against the configured derived address are complete, violating `signature verification must be domain-separated and malleability-safe so one signature cannot authorize multiple Starknet bridge actions`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_verify_borsh_signature`
- Entrypoint: `public signature-check path through `deploy_token` and `fin_transfer``
- Attacker controls: serialized Borsh payload bytes, signature `v/r/s`, and configured derived address
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect.
- Invariant to test: signature verification must be domain-separated and malleability-safe so one signature cannot authorize multiple Starknet bridge actions
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently.
