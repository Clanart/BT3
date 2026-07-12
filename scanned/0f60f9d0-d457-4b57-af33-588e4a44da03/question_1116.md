# Q1116: Keeper.GetNonce - Multi Message Tx Reads Stale Nonce For Second Message

## Question
Can an unprivileged attacker submit replay, reorder, or replacement transactions from attacker-controlled accounts through `nonce read for RPC, ante, and EVM execution` while controlling `deleted account sequence` and `multi-message order`, under the precondition that the tx batch contains reordered nonces, drive `SetCode authorization nonce bump -> tx sender nonce handling -> StateDB.Commit` in `x/evm/keeper/keeper.go::Keeper.GetNonce` so that multi-message tx reads stale nonce for second message, violating the invariant that contract creation nonce math must match geth, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/keeper.go::Keeper.GetNonce`
- Entrypoint: `nonce read for RPC, ante, and EVM execution`
- Attacker controls: `deleted account sequence`, `multi-message order`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: multi-message tx reads stale nonce for second message through `SetCode authorization nonce bump -> tx sender nonce handling -> StateDB.Commit`.
- Invariant to test: contract creation nonce math must match geth.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: compare IndexBlock/GetTransactionReceipt/GetBlockReceipts output against direct block/result reconstruction for the same transaction.
