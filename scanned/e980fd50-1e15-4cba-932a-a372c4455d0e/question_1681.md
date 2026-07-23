# Q1681: Exploit witness/annex edge cases in mid_state_txid

## Question
Can an unprivileged attacker exploit witness, annex, or script-path edge cases in the block/merkle proof nodes, indices, and ordering so `mid_state_txid` verifies a different Bitcoin statement than later settlement relies on, corrupting the kickoff/challenge/assert/disprove context treated as canonical for a deposit and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: circuits-lib/src/bridge_circuit/transaction.rs::mid_state_txid
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: make verification hash a different Bitcoin statement than settlement later uses via the block/merkle proof nodes, indices, and ordering
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
