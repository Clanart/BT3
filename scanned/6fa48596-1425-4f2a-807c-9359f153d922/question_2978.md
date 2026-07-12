# Q2978: Backend.GetBlockReceipts - Receipt List Omits Block Gas Exceeded Txs That Were Charged

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `eth_getBlockReceipts public JSON-RPC` while controlling `logs/bloom` and `ethTxIndex`, under the precondition that the transaction failed, reverted, or exceeded block gas after fee charge, drive `ApplyTransaction receipt/log output -> bloom/indexer storage -> public receipt/log query` in `rpc/backend/blocks.go::Backend.GetBlockReceipts` so that receipt list omits block-gas-exceeded txs that were charged, violating the invariant that receipts, logs, bloom, tx indexes, and gas used must identify the exact committed EVM result, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/blocks.go::Backend.GetBlockReceipts`
- Entrypoint: `eth_getBlockReceipts public JSON-RPC`
- Attacker controls: `logs/bloom`, `ethTxIndex`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: receipt list omits block-gas-exceeded txs that were charged through `ApplyTransaction receipt/log output -> bloom/indexer storage -> public receipt/log query`.
- Invariant to test: receipts, logs, bloom, tx indexes, and gas used must identify the exact committed EVM result.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: construct a contract harness that performs nested CALL/CREATE/SELFDESTRUCT/revert and compare bank keeper balances with StateDB balances.
