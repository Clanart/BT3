# Q3093: Corrupt work or canonical ordering in total_work_and_watchtower_flags_setup

## Question
Can an unprivileged attacker shape reorg timing around the same txid / outpoint / block height so `total_work_and_watchtower_flags_setup` computes or compares work / ordering incorrectly, causing the wrong canonical chain or watchtower result to win, corrupting the SPV inclusion result for the payout transaction and violating the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: circuits-lib/src/bridge_circuit/mod.rs::total_work_and_watchtower_flags_setup
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: reorg timing around the same txid / outpoint / block height
- Exploit idea: make the wrong chain or watchtower result win by shaping reorg timing around the same txid / outpoint / block height
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
