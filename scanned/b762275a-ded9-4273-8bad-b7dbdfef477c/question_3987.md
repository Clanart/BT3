# Q3987: Accept wrong proof/network context in save_get_block

## Question
Can an unprivileged attacker supply reorg timing around the same txid / outpoint / block height through broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation so `save_get_block` accepts it without fully binding network, method-id, genesis, or height context, corrupting the watchtower ordering / max-total-work decision used to judge operator honesty and breaking the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/bitcoin_syncer.rs::save_get_block
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: reorg timing around the same txid / outpoint / block height
- Exploit idea: omit full network, method-id, genesis, or height binding for reorg timing around the same txid / outpoint / block height
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
