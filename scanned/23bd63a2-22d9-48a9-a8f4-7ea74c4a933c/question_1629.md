# Q1629: Exploit witness/annex edge cases in fetch_new_blocks_backwards

## Question
Can an unprivileged attacker exploit witness, annex, or script-path edge cases in the block/merkle proof nodes, indices, and ordering so `fetch_new_blocks_backwards` verifies a different Bitcoin statement than later settlement relies on, corrupting the kickoff/challenge/assert/disprove context treated as canonical for a deposit and violating the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/bitcoin_syncer.rs::fetch_new_blocks_backwards
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: make verification hash a different Bitcoin statement than settlement later uses via the block/merkle proof nodes, indices, and ordering
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
