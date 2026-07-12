# Q3370: Keeper.SetTxBloom - Bloom Written Before Hook Failure Is Not Cleared

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `EVM log bloom persistence during ApplyTransaction` while controlling `tx hash` and `duplicate hash scenario`, under the precondition that multiple Ethereum messages appear in one Cosmos transaction, drive `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts` in `x/evm/keeper/bloom.go::Keeper.SetTxBloom` so that bloom written before hook failure is not cleared, violating the invariant that failed transactions must not be represented as successful fund transfers, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/bloom.go::Keeper.SetTxBloom`
- Entrypoint: `EVM log bloom persistence during ApplyTransaction`
- Attacker controls: `tx hash`, `duplicate hash scenario`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: bloom written before hook failure is not cleared through `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts`.
- Invariant to test: failed transactions must not be represented as successful fund transfers.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: run a local integration test that submits the crafted raw tx through JSON-RPC and compares committed state with direct keeper queries.
