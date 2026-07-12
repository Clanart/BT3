# Q838: Backend.GetBlockReceipts - Collectreceiptentriesfromblock Skips Mixed Cosmos Evm Txs

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `eth_getBlockReceipts public JSON-RPC` while controlling `tx result events` and `receipt status`, under the precondition that a Cronos-controlled accounting path consumes protocol receipt/log data, drive `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts` in `rpc/backend/blocks.go::Backend.GetBlockReceipts` so that collectReceiptEntriesFromBlock skips mixed Cosmos/EVM txs, violating the invariant that duplicate hashes or mixed messages must not overwrite receipt identity, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/blocks.go::Backend.GetBlockReceipts`
- Entrypoint: `eth_getBlockReceipts public JSON-RPC`
- Attacker controls: `tx result events`, `receipt status`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: collectReceiptEntriesFromBlock skips mixed Cosmos/EVM txs through `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts`.
- Invariant to test: duplicate hashes or mixed messages must not overwrite receipt identity.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: force the edge value at uint64/uint256/sdk.Int boundaries and assert no smaller debit, larger refund, or supply change occurs.
