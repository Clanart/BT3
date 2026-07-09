# Q2627: NEAR Wormhole prover verify_proof shared proof response reused across entrypoints

## Question
Can an unprivileged attacker obtain a valid verifier result for one public flow and reuse it in `public Wormhole proof verifier entrypoint` because `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof` trusts the same response envelope under a different meaning, violating `the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_proof`
- Entrypoint: `public Wormhole proof verifier entrypoint`
- Attacker controls: serialized `WormholeVerifyProofArgs`, VAA hex string, claimed proof kind, and timing against other proof submissions
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type.
- Invariant to test: the externally-approved VAA and the locally-parsed bridge message must remain identical in proof kind, emitter binding, and payload interpretation
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics.
