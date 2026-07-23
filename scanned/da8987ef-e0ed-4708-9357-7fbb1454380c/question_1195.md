# Q1195: Exploit ordering assumptions in work_only_from_header_chain_test

## Question
Can an unprivileged attacker use attacker-controlled the method-id, network, and genesis-context assumptions implied by the incoming proof so `work_only_from_header_chain_test` validates the right inputs in the wrong order, corrupting the watchtower ordering / max-total-work decision used to judge operator honesty and breaking the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: bridge-circuit-host/src/bridge_circuit_host.rs::work_only_from_header_chain_test
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the method-id, network, and genesis-context assumptions implied by the incoming proof
- Exploit idea: validate the right pieces in the wrong order using the method-id, network, and genesis-context assumptions implied by the incoming proof
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
