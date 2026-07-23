# Q1622: Exploit witness/annex edge cases in save_transaction_spent_utxos

## Question
Can an unprivileged attacker exploit witness, annex, or script-path edge cases in the header sequence, timestamps, and `bits` values so `save_transaction_spent_utxos` verifies a different Bitcoin statement than later settlement relies on, corrupting the SPV inclusion result for the payout transaction and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/bitcoin_syncer.rs::save_transaction_spent_utxos
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the header sequence, timestamps, and `bits` values
- Exploit idea: make verification hash a different Bitcoin statement than settlement later uses via the header sequence, timestamps, and `bits` values
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
