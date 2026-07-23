# Q678: Exploit reorg boundary handling in save_transaction_spent_utxos

## Question
Can an unprivileged attacker exploit reorg timing around Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents so `save_transaction_spent_utxos` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the kickoff/challenge/assert/disprove context treated as canonical for a deposit and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to High. Reorg-handling bug (Bitcoin-side or Citrea-side) that causes prolonged halt, inconsistent views, or unsafe rollback behavior?

## Target
- File/function: core/src/bitcoin_syncer.rs::save_transaction_spent_utxos
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: reorder or replay Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents across canonical and non-canonical views
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: High. Reorg-handling bug (Bitcoin-side or Citrea-side) that causes prolonged halt, inconsistent views, or unsafe rollback behavior
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
