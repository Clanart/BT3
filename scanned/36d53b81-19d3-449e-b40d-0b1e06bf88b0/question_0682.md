# Q682: Exploit reorg boundary handling in save_get_transaction_spent_utxos

## Question
Can an unprivileged attacker exploit reorg timing around multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context so `save_get_transaction_spent_utxos` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the storage-proof key/value binding used for deposit or withdrawal validation and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/bitcoin_syncer.rs::save_get_transaction_spent_utxos
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Exploit idea: reorder or replay multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context across canonical and non-canonical views
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
