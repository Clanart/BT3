# Q712: Exploit reorg boundary handling in lc_proof_verifier

## Question
Can an unprivileged attacker exploit reorg timing around the block/merkle proof nodes, indices, and ordering so `lc_proof_verifier` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the canonical header-chain state and total work and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: circuits-lib/src/bridge_circuit/lc_proof.rs::lc_proof_verifier
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: reorder or replay the block/merkle proof nodes, indices, and ordering across canonical and non-canonical views
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
