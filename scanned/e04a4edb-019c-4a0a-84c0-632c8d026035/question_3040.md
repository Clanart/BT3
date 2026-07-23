# Q3040: Corrupt work or canonical ordering in handle_reorg_events

## Question
Can an unprivileged attacker shape Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents so `handle_reorg_events` computes or compares work / ordering incorrectly, causing the wrong canonical chain or watchtower result to win, corrupting the SPV inclusion result for the payout transaction and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/bitcoin_syncer.rs::handle_reorg_events
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: make the wrong chain or watchtower result win by shaping Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
