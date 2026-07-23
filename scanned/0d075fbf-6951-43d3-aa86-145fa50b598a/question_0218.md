# Q218: Accept wrong proof/network context in prove_block_headers

## Question
Can an unprivileged attacker supply the method-id, network, and genesis-context assumptions implied by the incoming proof through broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation so `prove_block_headers` accepts it without fully binding network, method-id, genesis, or height context, corrupting the SPV inclusion result for the payout transaction and breaking the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/header_chain_prover.rs::prove_block_headers
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the method-id, network, and genesis-context assumptions implied by the incoming proof
- Exploit idea: omit full network, method-id, genesis, or height binding for the method-id, network, and genesis-context assumptions implied by the incoming proof
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
