# Q3680: Backend.GetBlockReceipts - Collectreceiptentriesfromblock Skips Mixed Cosmos Evm Txs

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `eth_getBlockReceipts public JSON-RPC` while controlling `msg index` and `ethTxIndex`, under the precondition that the block contains mixed Cosmos and Ethereum messages, drive `ApplyTransaction receipt/log output -> bloom/indexer storage -> public receipt/log query` in `rpc/backend/blocks.go::Backend.GetBlockReceipts` so that collectReceiptEntriesFromBlock skips mixed Cosmos/EVM txs, violating the invariant that indexed accounting must match direct block reconstruction, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/blocks.go::Backend.GetBlockReceipts`
- Entrypoint: `eth_getBlockReceipts public JSON-RPC`
- Attacker controls: `msg index`, `ethTxIndex`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: collectReceiptEntriesFromBlock skips mixed Cosmos/EVM txs through `ApplyTransaction receipt/log output -> bloom/indexer storage -> public receipt/log query`.
- Invariant to test: indexed accounting must match direct block reconstruction.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: build a two-message Cosmos tx fixture and assert ante, execution, refund, and receipt invariants after FinalizeBlock.
