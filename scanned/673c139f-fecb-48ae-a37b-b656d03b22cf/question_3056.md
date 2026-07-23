# Q3056: Corrupt work or canonical ordering in prove_block_headers_genesis

## Question
Can an unprivileged attacker shape the block/merkle proof nodes, indices, and ordering so `prove_block_headers_genesis` computes or compares work / ordering incorrectly, causing the wrong canonical chain or watchtower result to win, corrupting the storage-proof key/value binding used for deposit or withdrawal validation and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/header_chain_prover.rs::prove_block_headers_genesis
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: make the wrong chain or watchtower result win by shaping the block/merkle proof nodes, indices, and ordering
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
