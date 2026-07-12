# Q3213: Backend.GetLogs - Block Range Returns Logs Whose Receipt Status Is Failed

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `eth_getLogs or filter log retrieval` while controlling `ethTxIndex` and `receipt status`, under the precondition that the transaction failed, reverted, or exceeded block gas after fee charge, drive `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts` in `rpc/backend/filters.go::Backend.GetLogs` so that block range returns logs whose receipt status is failed, violating the invariant that duplicate hashes or mixed messages must not overwrite receipt identity, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/filters.go::Backend.GetLogs`
- Entrypoint: `eth_getLogs or filter log retrieval`
- Attacker controls: `ethTxIndex`, `receipt status`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: block range returns logs whose receipt status is failed through `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts`.
- Invariant to test: duplicate hashes or mixed messages must not overwrite receipt identity.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: add a fuzz case varying the attacker-controlled fields and differential-check against go-ethereum for tx validity, gas, nonce, and code/storage effects.
