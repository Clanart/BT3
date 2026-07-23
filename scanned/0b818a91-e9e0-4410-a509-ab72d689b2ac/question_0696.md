# Q696: Exploit reorg boundary handling in prove_block_headers_genesis

## Question
Can an unprivileged attacker exploit reorg timing around the header sequence, timestamps, and `bits` values so `prove_block_headers_genesis` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the watchtower ordering / max-total-work decision used to judge operator honesty and violating the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/header_chain_prover.rs::prove_block_headers_genesis
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the header sequence, timestamps, and `bits` values
- Exploit idea: reorder or replay the header sequence, timestamps, and `bits` values across canonical and non-canonical views
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
