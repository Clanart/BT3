# Q2976: KVIndexer.IndexBlock - Failed Block Gas Exceeded Tx Is Indexed As Charged With Gas Limit

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `block indexing of committed Ethereum transactions` while controlling `tx result events` and `msg index`, under the precondition that a Cronos-controlled accounting path consumes protocol receipt/log data, drive `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts` in `indexer/kv_indexer.go::KVIndexer.IndexBlock` so that failed block-gas-exceeded tx is indexed as charged with gas limit, violating the invariant that duplicate hashes or mixed messages must not overwrite receipt identity, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `indexer/kv_indexer.go::KVIndexer.IndexBlock`
- Entrypoint: `block indexing of committed Ethereum transactions`
- Attacker controls: `tx result events`, `msg index`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: failed block-gas-exceeded tx is indexed as charged with gas limit through `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts`.
- Invariant to test: duplicate hashes or mixed messages must not overwrite receipt identity.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: add a fuzz case varying the attacker-controlled fields and differential-check against go-ethereum for tx validity, gas, nonce, and code/storage effects.
