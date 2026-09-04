# Q2921: push_signature: epoch gate admits an unsupported auth mode

## Question
Can an unprivileged attacker reach `push_signature` (in `stacks-codec/src/transaction.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that `is_supported_in_epoch` differs between codec and stacks-transactions, breaking the invariant that transactions node A admits == transactions node B admits at one tip — leading to chain split from admission divergence?

## Target
- File/function: `stacks-codec/src/transaction.rs` -> `push_signature`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: `is_supported_in_epoch` differs between codec and stacks-transactions
- Invariant to test: transactions node A admits == transactions node B admits at one tip
- Expected Immunefi impact: Critical - chain split from admission divergence
- Fast validation: test an order-independent auth before its epoch
