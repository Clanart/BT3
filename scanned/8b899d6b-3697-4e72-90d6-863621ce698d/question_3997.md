# Q3997: NEAR verify_proof wrapper shared proof response reused across entrypoints through cross-module drift

## Question
Can an unprivileged attacker use `internal proof-dispatch helper reached from public `fin_transfer`, `claim_fee`, `deploy_token`, and `bind_token`` with control over chain kind, prover args bytes, and the choice of configured prover contract and desynchronize `near/omni-bridge/src/lib.rs::verify_proof` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `shared proof response reused across entrypoints` attack class because routes proof bytes to the chain-specific prover stored in `provers` and returns the promise used by higher-level bridge flows, violating `every proof-consuming public flow must stay bound to the intended prover, source chain, and proof kind so attackers cannot cross-wire verifier domains`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::verify_proof`
- Entrypoint: `internal proof-dispatch helper reached from public `fin_transfer`, `claim_fee`, `deploy_token`, and `bind_token``
- Attacker controls: chain kind, prover args bytes, and the choice of configured prover contract
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: every proof-consuming public flow must stay bound to the intended prover, source chain, and proof kind so attackers cannot cross-wire verifier domains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::verify_proof` and the adjacent proof parsing and source authentication after every branch.
