# Q49: NEAR Wormhole prover verify_vaa_callback state update before full validation

## Question
Can an unprivileged attacker exploit `callback after external Wormhole verification` so that `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback` mutates finalization state before all signature or proof checks implied by decodes hex, parses the VAA structure, checks that the first payload byte matches the expected proof kind, and converts it into a typed `ProverResult` are complete, violating `callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback`
- Entrypoint: `callback after external Wormhole verification`
- Attacker controls: proof kind, VAA bytes, callback success/failure, and local payload parsing
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect.
- Invariant to test: callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently.
