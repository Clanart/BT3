# Q1632: Exploit witness/annex edge cases in prove_work_only

## Question
Can an unprivileged attacker exploit witness, annex, or script-path edge cases in multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context so `prove_work_only` verifies a different Bitcoin statement than later settlement relies on, corrupting the canonical header-chain state and total work and violating the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/header_chain_prover.rs::prove_work_only
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Exploit idea: make verification hash a different Bitcoin statement than settlement later uses via multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
