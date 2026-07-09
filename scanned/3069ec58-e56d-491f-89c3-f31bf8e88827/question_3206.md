# Q3206: NEAR verify_proof wrapper optional-field encoding ambiguity

## Question
Can an unprivileged attacker exploit empty-versus-present optional fields in proofs reaching `internal proof-dispatch helper reached from public `fin_transfer`, `claim_fee`, `deploy_token`, and `bind_token`` so that `near/omni-bridge/src/lib.rs::verify_proof` authenticates one payload but downstream logic interprets another because of routes proof bytes to the chain-specific prover stored in `provers` and returns the promise used by higher-level bridge flows, violating `every proof-consuming public flow must stay bound to the intended prover, source chain, and proof kind so attackers cannot cross-wire verifier domains`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::verify_proof`
- Entrypoint: `internal proof-dispatch helper reached from public `fin_transfer`, `claim_fee`, `deploy_token`, and `bind_token``
- Attacker controls: chain kind, prover args bytes, and the choice of configured prover contract
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially.
- Invariant to test: every proof-consuming public flow must stay bound to the intended prover, source chain, and proof kind so attackers cannot cross-wire verifier domains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior.
