# Q2166: Substitute a wrong proof path into calculate_work

## Question
Can an unprivileged attacker substitute part of attacker-controlled the header sequence, timestamps, and `bits` values so `calculate_work` accepts a proof, header, or path that should have been rejected, corrupting the canonical header-chain state and total work and breaking the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency?

## Target
- File/function: circuits-lib/src/header_chain/mod.rs::calculate_work
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the header sequence, timestamps, and `bits` values
- Exploit idea: swap part of attacker-controlled the header sequence, timestamps, and `bits` values while keeping the rest seemingly valid
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
