# Q4658: `lc_proof_verifier` and in-circuit signature verification

## Question
Can a prover present witness data to `lc_proof_verifier` in `circuits-lib/src/bridge_circuit/lc_proof.rs` that satisfies its signature check while corresponding to a different message or key than the protocol intends - annex handling, sighash-type parsing from a 65-byte signature, prevout list construction, or an input index that selects the wrong prevout - so an unauthorised action is proved authorised?

## Target
- File/function: `circuits-lib/src/bridge_circuit/lc_proof.rs` -> `lc_proof_verifier` (This module implements the light client proof verifier for the bridge circuit)
- Entrypoint: attacker-crafted witness bytes inside a circuit input -> `lc_proof_verifier`
- Attacker controls: the witness stack, annex, sighash byte and prevout list; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: make the circuit accept a signature over a message the signer never approved
- Invariant to test: the message the circuit verifies the signature against == the sighash of the transaction and prevouts it is reasoning about
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: feed mismatched annex/prevout/sighash combinations and assert verification fails
