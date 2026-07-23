# Q3979: Accept wrong proof/network context in handle_new_block

## Question
Can an unprivileged attacker supply Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents through broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation so `handle_new_block` accepts it without fully binding network, method-id, genesis, or height context, corrupting the canonical header-chain state and total work and breaking the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/bitcoin_syncer.rs::handle_new_block
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: omit full network, method-id, genesis, or height binding for Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
