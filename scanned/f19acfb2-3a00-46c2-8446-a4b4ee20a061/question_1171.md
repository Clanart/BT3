# Q1171: Exploit ordering assumptions in prove_and_get_non_targeted_block

## Question
Can an unprivileged attacker use attacker-controlled the block/merkle proof nodes, indices, and ordering so `prove_and_get_non_targeted_block` validates the right inputs in the wrong order, corrupting the watchtower ordering / max-total-work decision used to judge operator honesty and breaking the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/header_chain_prover.rs::prove_and_get_non_targeted_block
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: validate the right pieces in the wrong order using the block/merkle proof nodes, indices, and ordering
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
