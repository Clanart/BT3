# Q1224: Exploit ordering assumptions in work_conversion

## Question
Can an unprivileged attacker use attacker-controlled multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context so `work_conversion` validates the right inputs in the wrong order, corrupting the kickoff/challenge/assert/disprove context treated as canonical for a deposit and breaking the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: circuits-lib/src/work_only/mod.rs::work_conversion
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Exploit idea: validate the right pieces in the wrong order using multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
