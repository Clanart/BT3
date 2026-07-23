# Q3108: Corrupt work or canonical ordering in apply_block_headers

## Question
Can an unprivileged attacker shape the block/merkle proof nodes, indices, and ordering so `apply_block_headers` computes or compares work / ordering incorrectly, causing the wrong canonical chain or watchtower result to win, corrupting the SPV inclusion result for the payout transaction and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to High. Reorg-handling bug (Bitcoin-side or Citrea-side) that causes prolonged halt, inconsistent views, or unsafe rollback behavior?

## Target
- File/function: circuits-lib/src/header_chain/mod.rs::apply_block_headers
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: make the wrong chain or watchtower result win by shaping the block/merkle proof nodes, indices, and ordering
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: High. Reorg-handling bug (Bitcoin-side or Citrea-side) that causes prolonged halt, inconsistent views, or unsafe rollback behavior
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
