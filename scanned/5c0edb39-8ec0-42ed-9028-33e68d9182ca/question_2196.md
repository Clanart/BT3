# Q2196: KVIndexer.IndexBlock - Failed Block Gas Exceeded Tx Is Indexed As Charged With Gas Limit

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `block indexing of committed Ethereum transactions` while controlling `ethTxIndex` and `tx hash`, under the precondition that the transaction failed, reverted, or exceeded block gas after fee charge, drive `ApplyTransaction receipt/log output -> bloom/indexer storage -> public receipt/log query` in `indexer/kv_indexer.go::KVIndexer.IndexBlock` so that failed block-gas-exceeded tx is indexed as charged with gas limit, violating the invariant that receipts, logs, bloom, tx indexes, and gas used must identify the exact committed EVM result, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `indexer/kv_indexer.go::KVIndexer.IndexBlock`
- Entrypoint: `block indexing of committed Ethereum transactions`
- Attacker controls: `ethTxIndex`, `tx hash`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: failed block-gas-exceeded tx is indexed as charged with gas limit through `ApplyTransaction receipt/log output -> bloom/indexer storage -> public receipt/log query`.
- Invariant to test: receipts, logs, bloom, tx indexes, and gas used must identify the exact committed EVM result.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: construct a contract harness that performs nested CALL/CREATE/SELFDESTRUCT/revert and compare bank keeper balances with StateDB balances.
