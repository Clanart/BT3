# Q4142: new: multisig verifies with fewer distinct signers than required

## Question
Can an unprivileged attacker reach `new` (in `stacks-common/src/util/secp256k1/native.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that a duplicated signature or key field fills a slot without a real signature, breaking the invariant that distinct verified signatures over the sighash == signatures_required — leading to auth bypass on a multisig account?

## Target
- File/function: `stacks-common/src/util/secp256k1/native.rs` -> `new`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: a duplicated signature or key field fills a slot without a real signature
- Invariant to test: distinct verified signatures over the sighash == signatures_required
- Expected Immunefi impact: Critical - auth bypass on a multisig account
- Fast validation: test an under-signed multisig auth
