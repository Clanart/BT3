# Q0549: stx_transfer_consolidated: NFT post-condition compares id under a different encoding

## Question
Can an unprivileged attacker reach `stx_transfer_consolidated` (in `clarity/src/vm/functions/assets.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that `AssetInfoID`/`Value` id comparison differs from the AssetMap's, breaking the invariant that the id matched by a post-condition == the id in the committed AssetMap — leading to NFT theft past a post-condition?

## Target
- File/function: `clarity/src/vm/functions/assets.rs` -> `stx_transfer_consolidated`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: `AssetInfoID`/`Value` id comparison differs from the AssetMap's
- Invariant to test: the id matched by a post-condition == the id in the committed AssetMap
- Expected Immunefi impact: Critical - NFT theft past a post-condition
- Fast validation: test an NFT id encoding mismatch
