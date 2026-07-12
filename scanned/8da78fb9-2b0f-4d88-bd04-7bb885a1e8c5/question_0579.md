# Q579: Backend.GetTransactionReceipt - Failed Evm Tx Reports Success After Event Mismatch

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `eth_getTransactionReceipt by hash or block-scoped lookup` while controlling `receipt status` and `ethTxIndex`, under the precondition that a Cronos-controlled accounting path consumes protocol receipt/log data, drive `ApplyTransaction receipt/log output -> bloom/indexer storage -> public receipt/log query` in `rpc/backend/tx_info.go::Backend.GetTransactionReceipt` so that failed EVM tx reports success after event mismatch, violating the invariant that receipts, logs, bloom, tx indexes, and gas used must identify the exact committed EVM result, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/tx_info.go::Backend.GetTransactionReceipt`
- Entrypoint: `eth_getTransactionReceipt by hash or block-scoped lookup`
- Attacker controls: `receipt status`, `ethTxIndex`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: failed EVM tx reports success after event mismatch through `ApplyTransaction receipt/log output -> bloom/indexer storage -> public receipt/log query`.
- Invariant to test: receipts, logs, bloom, tx indexes, and gas used must identify the exact committed EVM result.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: write a focused Go unit test around the target function and assert bank supply, sender balance, nonce, code hash, logs, and receipt status before and after.
