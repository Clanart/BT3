# Q3037: Corrupt work or canonical ordering in save_block

## Question
Can an unprivileged attacker shape multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context so `save_block` computes or compares work / ordering incorrectly, causing the wrong canonical chain or watchtower result to win, corrupting the watchtower ordering / max-total-work decision used to judge operator honesty and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/bitcoin_syncer.rs::save_block
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Exploit idea: make the wrong chain or watchtower result win by shaping multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
