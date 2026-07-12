# Q1181: Backend.GetTransactionReceipt - Block Scoped Rebuild Disagrees With Indexer Lookup

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `eth_getTransactionReceipt by hash or block-scoped lookup` while controlling `msg index` and `tx hash`, under the precondition that the block contains mixed Cosmos and Ethereum messages, drive `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts` in `rpc/backend/tx_info.go::Backend.GetTransactionReceipt` so that block-scoped rebuild disagrees with indexer lookup, violating the invariant that failed transactions must not be represented as successful fund transfers, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/tx_info.go::Backend.GetTransactionReceipt`
- Entrypoint: `eth_getTransactionReceipt by hash or block-scoped lookup`
- Attacker controls: `msg index`, `tx hash`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: block-scoped rebuild disagrees with indexer lookup through `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts`.
- Invariant to test: failed transactions must not be represented as successful fund transfers.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: run a local integration test that submits the crafted raw tx through JSON-RPC and compares committed state with direct keeper queries.
