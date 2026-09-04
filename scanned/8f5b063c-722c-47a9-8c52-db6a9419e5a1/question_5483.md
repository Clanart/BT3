# Q5483: from_runtime_failure_contract_call: post-condition mode gated differently across codepaths

## Question
Can an unprivileged attacker reach `from_runtime_failure_contract_call` (in `stackslib/src/chainstate/stacks/db/transactions.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that codec vs stacks-transactions classify a mode differently by epoch, breaking the invariant that the mode a tx is judged under == one consistent classification — leading to admission divergence?

## Target
- File/function: `stackslib/src/chainstate/stacks/db/transactions.rs` -> `from_runtime_failure_contract_call`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: codec vs stacks-transactions classify a mode differently by epoch
- Invariant to test: the mode a tx is judged under == one consistent classification
- Expected Immunefi impact: Critical - admission divergence
- Fast validation: test a mode at an epoch edge
