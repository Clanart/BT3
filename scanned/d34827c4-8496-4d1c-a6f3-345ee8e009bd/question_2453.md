# Q2453: NEAR deploy_token callback malicious metadata manufactures a bridge identity at boundary values

## Question
Can an unprivileged attacker trigger `proof callback for public `deploy_token`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::deploy_token_callback` violate `proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings` in the `malicious metadata manufactures a bridge identity` attack class because checks the emitting factory, then calls `deploy_token_internal` using metadata from the validated message becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_callback`
- Entrypoint: `proof callback for public `deploy_token``
- Attacker controls: decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
