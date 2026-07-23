# Q2120: Substitute a wrong proof path into verify_proof

## Question
Can an unprivileged attacker substitute part of attacker-controlled the method-id, network, and genesis-context assumptions implied by the incoming proof so `verify_proof` accepts a proof, header, or path that should have been rejected, corrupting the watchtower ordering / max-total-work decision used to judge operator honesty and breaking the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: circuits-lib/src/header_chain/mmr_guest.rs::verify_proof
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the method-id, network, and genesis-context assumptions implied by the incoming proof
- Exploit idea: swap part of attacker-controlled the method-id, network, and genesis-context assumptions implied by the incoming proof while keeping the rest seemingly valid
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
