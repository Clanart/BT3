# Q1997: NEAR deploy_token callback malicious metadata manufactures a bridge identity

## Question
Can an unprivileged attacker invoke `proof callback for public `deploy_token`` with a malicious token or metadata payload so that `near/omni-bridge/src/lib.rs::deploy_token_callback` records a deceptive asset identity that later drives deployment or claims, violating `proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::deploy_token_callback`
- Entrypoint: `proof callback for public `deploy_token``
- Attacker controls: decoded `LogMetadata` result, chain kind, token address, metadata contents, and attached deposit
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs.
- Invariant to test: proof-bound metadata must not be replayable or malleable in a way that deploys the wrong token id, wrong decimals, or conflicting mappings
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals.
