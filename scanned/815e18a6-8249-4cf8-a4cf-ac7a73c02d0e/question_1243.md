# Q1243: expects_sortition: nonce not advanced so the transaction is replayable

## Question
Can an unprivileged attacker reach `expects_sortition` (in `stacks-codec/src/transaction.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that `check_transaction_nonces`/`update_account_nonce` leaves the nonce unchanged on a path, breaking the invariant that nonce after a tx == committed nonce + 1 — leading to transaction replay?

## Target
- File/function: `stacks-codec/src/transaction.rs` -> `expects_sortition`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: `check_transaction_nonces`/`update_account_nonce` leaves the nonce unchanged on a path
- Invariant to test: nonce after a tx == committed nonce + 1
- Expected Immunefi impact: Critical - transaction replay
- Fast validation: test a payload path asserting nonce advance
