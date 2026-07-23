# Q3041: Corrupt work or canonical ordering in process_new_blocks

## Question
Can an unprivileged attacker shape the method-id, network, and genesis-context assumptions implied by the incoming proof so `process_new_blocks` computes or compares work / ordering incorrectly, causing the wrong canonical chain or watchtower result to win, corrupting the canonical header-chain state and total work and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/bitcoin_syncer.rs::process_new_blocks
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the method-id, network, and genesis-context assumptions implied by the incoming proof
- Exploit idea: make the wrong chain or watchtower result win by shaping the method-id, network, and genesis-context assumptions implied by the incoming proof
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
