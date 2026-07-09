# Q368: NEAR verify_proof wrapper state update before full validation through cross-module drift

## Question
Can an unprivileged attacker use `internal proof-dispatch helper reached from public `fin_transfer`, `claim_fee`, `deploy_token`, and `bind_token`` with control over chain kind, prover args bytes, and the choice of configured prover contract and desynchronize `near/omni-bridge/src/lib.rs::verify_proof` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `state update before full validation` attack class because routes proof bytes to the chain-specific prover stored in `provers` and returns the promise used by higher-level bridge flows, violating `every proof-consuming public flow must stay bound to the intended prover, source chain, and proof kind so attackers cannot cross-wire verifier domains`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::verify_proof`
- Entrypoint: `internal proof-dispatch helper reached from public `fin_transfer`, `claim_fee`, `deploy_token`, and `bind_token``
- Attacker controls: chain kind, prover args bytes, and the choice of configured prover contract
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: every proof-consuming public flow must stay bound to the intended prover, source chain, and proof kind so attackers cannot cross-wire verifier domains
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::verify_proof` and the adjacent proof parsing and source authentication after every branch.
