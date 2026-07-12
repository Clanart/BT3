# Q2043: Backend.GetLogs - Block Range Returns Logs Whose Receipt Status Is Failed

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `eth_getLogs or filter log retrieval` while controlling `gas used` and `tx hash`, under the precondition that the block contains mixed Cosmos and Ethereum messages, drive `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts` in `rpc/backend/filters.go::Backend.GetLogs` so that block range returns logs whose receipt status is failed, violating the invariant that failed transactions must not be represented as successful fund transfers, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/filters.go::Backend.GetLogs`
- Entrypoint: `eth_getLogs or filter log retrieval`
- Attacker controls: `gas used`, `tx hash`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: block range returns logs whose receipt status is failed through `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts`.
- Invariant to test: failed transactions must not be represented as successful fund transfers.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: run a local integration test that submits the crafted raw tx through JSON-RPC and compares committed state with direct keeper queries.
