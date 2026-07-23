# Q1667: Exploit witness/annex edge cases in work_only_from_header_chain_test

## Question
Can an unprivileged attacker exploit witness, annex, or script-path edge cases in reorg timing around the same txid / outpoint / block height so `work_only_from_header_chain_test` verifies a different Bitcoin statement than later settlement relies on, corrupting the kickoff/challenge/assert/disprove context treated as canonical for a deposit and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: bridge-circuit-host/src/bridge_circuit_host.rs::work_only_from_header_chain_test
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: reorg timing around the same txid / outpoint / block height
- Exploit idea: make verification hash a different Bitcoin statement than settlement later uses via reorg timing around the same txid / outpoint / block height
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
