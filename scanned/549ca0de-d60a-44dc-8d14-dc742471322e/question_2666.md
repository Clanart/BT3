# Q2666: Backend.GetBlockReceipts - Raw And Json Receipts Encode Different Status

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `eth_getBlockReceipts public JSON-RPC` while controlling `ethTxIndex` and `logs/bloom`, under the precondition that the transaction failed, reverted, or exceeded block gas after fee charge, drive `block-scoped receipt rebuild -> TxResult lookup -> Cronos-controlled accounting consumer` in `rpc/backend/blocks.go::Backend.GetBlockReceipts` so that raw and JSON receipts encode different status, violating the invariant that receipts, logs, bloom, tx indexes, and gas used must identify the exact committed EVM result, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/blocks.go::Backend.GetBlockReceipts`
- Entrypoint: `eth_getBlockReceipts public JSON-RPC`
- Attacker controls: `ethTxIndex`, `logs/bloom`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: raw and JSON receipts encode different status through `block-scoped receipt rebuild -> TxResult lookup -> Cronos-controlled accounting consumer`.
- Invariant to test: receipts, logs, bloom, tx indexes, and gas used must identify the exact committed EVM result.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: write a focused Go unit test around the target function and assert bank supply, sender balance, nonce, code hash, logs, and receipt status before and after.
