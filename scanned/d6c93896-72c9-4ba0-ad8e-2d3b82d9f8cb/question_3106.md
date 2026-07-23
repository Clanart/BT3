# Q3106: Corrupt work or canonical ordering in compute_block_hash

## Question
Can an unprivileged attacker shape the block/merkle proof nodes, indices, and ordering so `compute_block_hash` computes or compares work / ordering incorrectly, causing the wrong canonical chain or watchtower result to win, corrupting the canonical header-chain state and total work and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: circuits-lib/src/header_chain/mod.rs::compute_block_hash
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: make the wrong chain or watchtower result win by shaping the block/merkle proof nodes, indices, and ordering
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
