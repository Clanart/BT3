# Q274: Accept wrong proof/network context in compute_block_hash

## Question
Can an unprivileged attacker supply the block/merkle proof nodes, indices, and ordering through broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation so `compute_block_hash` accepts it without fully binding network, method-id, genesis, or height context, corrupting the canonical header-chain state and total work and breaking the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: circuits-lib/src/header_chain/mod.rs::compute_block_hash
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: omit full network, method-id, genesis, or height binding for the block/merkle proof nodes, indices, and ordering
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
