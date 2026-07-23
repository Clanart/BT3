# Q1660: Exploit witness/annex edge cases in deposit_constant

## Question
Can an unprivileged attacker exploit witness, annex, or script-path edge cases in the block/merkle proof nodes, indices, and ordering so `deposit_constant` verifies a different Bitcoin statement than later settlement relies on, corrupting the SPV inclusion result for the payout transaction and violating the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: circuits-lib/src/bridge_circuit/mod.rs::deposit_constant
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: make verification hash a different Bitcoin statement than settlement later uses via the block/merkle proof nodes, indices, and ordering
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
