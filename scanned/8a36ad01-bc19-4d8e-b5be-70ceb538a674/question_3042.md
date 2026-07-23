# Q3042: Corrupt work or canonical ordering in save_get_transaction_spent_utxos

## Question
Can an unprivileged attacker shape reorg timing around the same txid / outpoint / block height so `save_get_transaction_spent_utxos` computes or compares work / ordering incorrectly, causing the wrong canonical chain or watchtower result to win, corrupting the L1 block hash carried from the light-client proof into bridge validation and violating the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/bitcoin_syncer.rs::save_get_transaction_spent_utxos
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: reorg timing around the same txid / outpoint / block height
- Exploit idea: make the wrong chain or watchtower result win by shaping reorg timing around the same txid / outpoint / block height
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
