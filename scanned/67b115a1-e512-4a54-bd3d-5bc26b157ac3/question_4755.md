# Q4755: `create_disprove_txhandler` and a payout block hash committed before it is final

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction get a payout transaction into a block, let `create_disprove_txhandler` in `core/src/builder/transaction/challenge.rs` commit that block hash, and then invalidate it - by a conflicting spend of the same withdrawal UTXO that lands in a competing branch, or an RBF replacement - so the value committed on-chain can no longer be proved and the corresponding vault becomes unreachable?

## Target
- File/function: `core/src/builder/transaction/challenge.rs` -> `create_disprove_txhandler` (This module provides functions for constructing and challenge related transactions in the protocol)
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `create_disprove_txhandler`
- Attacker controls: the competing transaction, its fee, and its timing relative to confirmation depth; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: make an already-committed settlement claim unprovable and the vault it points at unspendable
- Invariant to test: the block hash committed for a payout == the block containing that payout in the chain later proved
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: regtest: reorg out a payout after the commitment and assert the settlement path is still reachable
