# Q3582: Drive state split inside calculate_work

## Question
Can an unprivileged attacker use broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation with crafted multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context so `calculate_work` updates one canonical value while another subsystem retains the older one for the same event, corrupting the storage-proof key/value binding used for deposit or withdrawal validation and breaking the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: circuits-lib/src/header_chain/mod.rs::calculate_work
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Exploit idea: update one canonical value while another subsystem keeps the old one for the same event via multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
