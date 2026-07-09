# Q1367: NEAR verify_proof wrapper missing chain or contract domain separation

## Question
Can an unprivileged attacker reuse a valid proof or signature from one chain, contract, or message domain in `internal proof-dispatch helper reached from public `fin_transfer`, `claim_fee`, `deploy_token`, and `bind_token`` because `near/omni-bridge/src/lib.rs::verify_proof` relies on routes proof bytes to the chain-specific prover stored in `provers` and returns the promise used by higher-level bridge flows more narrowly than the true trust domain, violating `every proof-consuming public flow must stay bound to the intended prover, source chain, and proof kind so attackers cannot cross-wire verifier domains`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::verify_proof`
- Entrypoint: `internal proof-dispatch helper reached from public `fin_transfer`, `claim_fee`, `deploy_token`, and `bind_token``
- Attacker controls: chain kind, prover args bytes, and the choice of configured prover contract
- Exploit idea: Target validators keyed by derived signer, block hash, emitter address, or payload bytes that omit some domain field.
- Invariant to test: every proof-consuming public flow must stay bound to the intended prover, source chain, and proof kind so attackers cannot cross-wire verifier domains
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt cross-chain and cross-contract replay of the same validated bytes and assert that every trust domain field participates in acceptance.
