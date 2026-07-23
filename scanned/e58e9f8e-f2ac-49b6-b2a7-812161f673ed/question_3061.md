# Q3061: Corrupt work or canonical ordering in verify_merkle_proof

## Question
Can an unprivileged attacker shape the method-id, network, and genesis-context assumptions implied by the incoming proof so `verify_merkle_proof` computes or compares work / ordering incorrectly, causing the wrong canonical chain or watchtower result to win, corrupting the SPV inclusion result for the payout transaction and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: circuits-lib/src/bridge_circuit/merkle_tree.rs::verify_merkle_proof
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the method-id, network, and genesis-context assumptions implied by the incoming proof
- Exploit idea: make the wrong chain or watchtower result win by shaping the method-id, network, and genesis-context assumptions implied by the incoming proof
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
