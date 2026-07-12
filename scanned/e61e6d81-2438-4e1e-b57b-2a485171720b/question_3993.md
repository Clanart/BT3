# Q3993: Backend.GetLogs - Block Range Returns Logs Whose Receipt Status Is Failed

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `eth_getLogs or filter log retrieval` while controlling `tx result events` and `ethTxIndex`, under the precondition that a Cronos-controlled accounting path consumes protocol receipt/log data, drive `block-scoped receipt rebuild -> TxResult lookup -> Cronos-controlled accounting consumer` in `rpc/backend/filters.go::Backend.GetLogs` so that block range returns logs whose receipt status is failed, violating the invariant that receipts, logs, bloom, tx indexes, and gas used must identify the exact committed EVM result, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/filters.go::Backend.GetLogs`
- Entrypoint: `eth_getLogs or filter log retrieval`
- Attacker controls: `tx result events`, `ethTxIndex`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: block range returns logs whose receipt status is failed through `block-scoped receipt rebuild -> TxResult lookup -> Cronos-controlled accounting consumer`.
- Invariant to test: receipts, logs, bloom, tx indexes, and gas used must identify the exact committed EVM result.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: write a focused Go unit test around the target function and assert bank supply, sender balance, nonce, code hash, logs, and receipt status before and after.
