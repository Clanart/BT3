# Q3989: Accept wrong proof/network context in fetch_new_blocks_backwards

## Question
Can an unprivileged attacker supply Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents through broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation so `fetch_new_blocks_backwards` accepts it without fully binding network, method-id, genesis, or height context, corrupting the watchtower ordering / max-total-work decision used to judge operator honesty and breaking the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/bitcoin_syncer.rs::fetch_new_blocks_backwards
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: omit full network, method-id, genesis, or height binding for Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
