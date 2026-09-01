# Q3192: `get_citrea_l2_height_range` and a payout block hash committed before it is final

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction get a payout transaction into a block, let `get_citrea_l2_height_range` in `core/src/citrea.rs` commit that block hash, and then invalidate it - by a conflicting spend of the same withdrawal UTXO that lands in a competing branch, or an RBF replacement - so the value committed on-chain can no longer be proved and the corresponding vault becomes unreachable?

## Target
- File/function: `core/src/citrea.rs` -> `get_citrea_l2_height_range`
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `get_citrea_l2_height_range`
- Attacker controls: the competing transaction, its fee, and its timing relative to confirmation depth; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: make an already-committed settlement claim unprovable and the vault it points at unspendable
- Invariant to test: the block hash committed for a payout == the block containing that payout in the chain later proved
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: regtest: reorg out a payout after the commitment and assert the settlement path is still reachable
