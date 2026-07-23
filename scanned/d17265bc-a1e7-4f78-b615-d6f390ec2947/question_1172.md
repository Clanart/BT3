# Q1172: Exploit ordering assumptions in verify_storage_proofs

## Question
Can an unprivileged attacker use attacker-controlled reorg timing around the same txid / outpoint / block height so `verify_storage_proofs` validates the right inputs in the wrong order, corrupting the kickoff/challenge/assert/disprove context treated as canonical for a deposit and breaking the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: circuits-lib/src/bridge_circuit/storage_proof.rs::verify_storage_proofs
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: reorg timing around the same txid / outpoint / block height
- Exploit idea: validate the right pieces in the wrong order using reorg timing around the same txid / outpoint / block height
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
