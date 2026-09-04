# Q0185: special_get_token_supply: sponsored fee charged to the wrong account

## Question
Can an unprivileged attacker reach `special_get_token_supply` (in `clarity/src/vm/functions/assets.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that the fee is debited from origin instead of sponsor (or vice versa), breaking the invariant that account debited the fee == the sponsor who signed the sponsor auth — leading to unauthorised fee charge?

## Target
- File/function: `clarity/src/vm/functions/assets.rs` -> `special_get_token_supply`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: the fee is debited from origin instead of sponsor (or vice versa)
- Invariant to test: account debited the fee == the sponsor who signed the sponsor auth
- Expected Immunefi impact: Critical - unauthorised fee charge
- Fast validation: test a sponsored tx asserting who pays
