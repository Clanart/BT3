# Q1187: Exploit ordering assumptions in convert_to_groth16_and_verify

## Question
Can an unprivileged attacker use attacker-controlled the method-id, network, and genesis-context assumptions implied by the incoming proof so `convert_to_groth16_and_verify` validates the right inputs in the wrong order, corrupting the SPV inclusion result for the payout transaction and breaking the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: circuits-lib/src/bridge_circuit/mod.rs::convert_to_groth16_and_verify
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the method-id, network, and genesis-context assumptions implied by the incoming proof
- Exploit idea: validate the right pieces in the wrong order using the method-id, network, and genesis-context assumptions implied by the incoming proof
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
