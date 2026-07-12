# Q2979: Backend.GetLogs - Logs From Failed Post Hook Tx Remain Queryable As Success Evidence

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `eth_getLogs or filter log retrieval` while controlling `msg index` and `duplicate hash scenario`, under the precondition that the block contains mixed Cosmos and Ethereum messages, drive `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts` in `rpc/backend/filters.go::Backend.GetLogs` so that logs from failed post-hook tx remain queryable as success evidence, violating the invariant that failed transactions must not be represented as successful fund transfers, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/filters.go::Backend.GetLogs`
- Entrypoint: `eth_getLogs or filter log retrieval`
- Attacker controls: `msg index`, `duplicate hash scenario`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: logs from failed post-hook tx remain queryable as success evidence through `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts`.
- Invariant to test: failed transactions must not be represented as successful fund transfers.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: replay the same scenario through eth_call or estimateGas and through eth_sendRawTransaction and assert the only difference is persistence.
