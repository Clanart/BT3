# Q5917: clear_before_coinbase_height: post-condition list altered after signing still executes

## Question
Can an unprivileged attacker reach `clear_before_coinbase_height` (in `stackslib/src/core/mempool.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that the post-condition list/mode is outside the sighash, breaking the invariant that post-conditions enforced == those the signer committed — leading to theft by stripping a post-condition?

## Target
- File/function: `stackslib/src/core/mempool.rs` -> `clear_before_coinbase_height`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: the post-condition list/mode is outside the sighash
- Invariant to test: post-conditions enforced == those the signer committed
- Expected Immunefi impact: Critical - theft by stripping a post-condition
- Fast validation: test mutating post-conditions post-signing
