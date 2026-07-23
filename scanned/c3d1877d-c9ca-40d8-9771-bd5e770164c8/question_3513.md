# Q3513: Drive state split inside process_new_blocks

## Question
Can an unprivileged attacker use broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation with crafted reorg timing around the same txid / outpoint / block height so `process_new_blocks` updates one canonical value while another subsystem retains the older one for the same event, corrupting the SPV inclusion result for the payout transaction and breaking the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/bitcoin_syncer.rs::process_new_blocks
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: reorg timing around the same txid / outpoint / block height
- Exploit idea: update one canonical value while another subsystem keeps the old one for the same event via reorg timing around the same txid / outpoint / block height
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
