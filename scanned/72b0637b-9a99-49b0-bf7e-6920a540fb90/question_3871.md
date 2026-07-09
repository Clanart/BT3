# Q3871: NEAR verify_proof wrapper shared proof response reused across entrypoints via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal proof-dispatch helper reached from public `fin_transfer`, `claim_fee`, `deploy_token`, and `bind_token`` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-bridge/src/lib.rs::verify_proof` ends up accepting two inconsistent interpretations of the same economic event specifically around `shared proof response reused across entrypoints` under routes proof bytes to the chain-specific prover stored in `provers` and returns the promise used by higher-level bridge flows, violating `every proof-consuming public flow must stay bound to the intended prover, source chain, and proof kind so attackers cannot cross-wire verifier domains`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::verify_proof`
- Entrypoint: `internal proof-dispatch helper reached from public `fin_transfer`, `claim_fee`, `deploy_token`, and `bind_token``
- Attacker controls: chain kind, prover args bytes, and the choice of configured prover contract
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: every proof-consuming public flow must stay bound to the intended prover, source chain, and proof kind so attackers cannot cross-wire verifier domains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
