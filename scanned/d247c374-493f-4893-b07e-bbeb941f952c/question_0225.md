# Q225: Accept wrong proof/network context in prove_block_headers_second

## Question
Can an unprivileged attacker supply the block/merkle proof nodes, indices, and ordering through broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation so `prove_block_headers_second` accepts it without fully binding network, method-id, genesis, or height context, corrupting the L1 block hash carried from the light-client proof into bridge validation and breaking the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/header_chain_prover.rs::prove_block_headers_second
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: omit full network, method-id, genesis, or height binding for the block/merkle proof nodes, indices, and ordering
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
