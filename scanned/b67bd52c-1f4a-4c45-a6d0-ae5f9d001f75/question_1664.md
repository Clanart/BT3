# Q1664: Exploit witness/annex edge cases in verify

## Question
Can an unprivileged attacker exploit witness, annex, or script-path edge cases in multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context so `verify` verifies a different Bitcoin statement than later settlement relies on, corrupting the storage-proof key/value binding used for deposit or withdrawal validation and violating the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: circuits-lib/src/common/zkvm.rs::verify
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Exploit idea: make verification hash a different Bitcoin statement than settlement later uses via multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
