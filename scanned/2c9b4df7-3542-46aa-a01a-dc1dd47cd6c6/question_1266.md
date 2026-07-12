# Q1266: KVIndexer.IndexBlock - Duplicate Ethereum Tx Hash Overwrites Earlier Txresult

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `block indexing of committed Ethereum transactions` while controlling `msg index` and `gas used`, under the precondition that the block contains mixed Cosmos and Ethereum messages, drive `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts` in `indexer/kv_indexer.go::KVIndexer.IndexBlock` so that duplicate Ethereum tx hash overwrites earlier TxResult, violating the invariant that indexed accounting must match direct block reconstruction, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `indexer/kv_indexer.go::KVIndexer.IndexBlock`
- Entrypoint: `block indexing of committed Ethereum transactions`
- Attacker controls: `msg index`, `gas used`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: duplicate Ethereum tx hash overwrites earlier TxResult through `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts`.
- Invariant to test: indexed accounting must match direct block reconstruction.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: compare IndexBlock/GetTransactionReceipt/GetBlockReceipts output against direct block/result reconstruction for the same transaction.
