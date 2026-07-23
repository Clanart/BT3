# Q750: Exploit reorg boundary handling in calculate_work

## Question
Can an unprivileged attacker exploit reorg timing around multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context so `calculate_work` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the storage-proof key/value binding used for deposit or withdrawal validation and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: circuits-lib/src/header_chain/mod.rs::calculate_work
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Exploit idea: reorder or replay multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context across canonical and non-canonical views
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
