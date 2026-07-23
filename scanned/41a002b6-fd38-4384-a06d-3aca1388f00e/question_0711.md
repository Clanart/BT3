# Q711: Exploit reorg boundary handling in verify

## Question
Can an unprivileged attacker exploit reorg timing around reorg timing around the same txid / outpoint / block height so `verify` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the SPV inclusion result for the payout transaction and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: circuits-lib/src/bridge_circuit/groth16_verifier.rs::verify
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: reorg timing around the same txid / outpoint / block height
- Exploit idea: reorder or replay reorg timing around the same txid / outpoint / block height across canonical and non-canonical views
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
