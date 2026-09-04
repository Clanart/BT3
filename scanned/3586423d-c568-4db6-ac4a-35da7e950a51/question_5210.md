# Q5210: check_transaction_nonces: clarity version newer than epoch slips a soft check

## Question
Can an unprivileged attacker reach `check_transaction_nonces` (in `stackslib/src/chainstate/stacks/db/transactions.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that `SmartContract(_, Some(version))` exceeds `default_for_epoch` but is admitted, breaking the invariant that the Clarity version admitted <= the epoch max — leading to admission divergence?

## Target
- File/function: `stackslib/src/chainstate/stacks/db/transactions.rs` -> `check_transaction_nonces`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: `SmartContract(_, Some(version))` exceeds `default_for_epoch` but is admitted
- Invariant to test: the Clarity version admitted <= the epoch max
- Expected Immunefi impact: Critical - admission divergence
- Fast validation: test an over-version deploy
