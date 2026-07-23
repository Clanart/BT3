# Q3111: Corrupt work or canonical ordering in work_only_circuit

## Question
Can an unprivileged attacker shape multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context so `work_only_circuit` computes or compares work / ordering incorrectly, causing the wrong canonical chain or watchtower result to win, corrupting the kickoff/challenge/assert/disprove context treated as canonical for a deposit and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: circuits-lib/src/work_only/mod.rs::work_only_circuit
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Exploit idea: make the wrong chain or watchtower result win by shaping multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
