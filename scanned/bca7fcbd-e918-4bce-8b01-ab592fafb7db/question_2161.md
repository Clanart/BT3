# Q2161: Substitute a wrong proof path into header_chain_circuit

## Question
Can an unprivileged attacker substitute part of attacker-controlled the block/merkle proof nodes, indices, and ordering so `header_chain_circuit` accepts a proof, header, or path that should have been rejected, corrupting the canonical header-chain state and total work and breaking the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: circuits-lib/src/header_chain/mod.rs::header_chain_circuit
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: swap part of attacker-controlled the block/merkle proof nodes, indices, and ordering while keeping the rest seemingly valid
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
