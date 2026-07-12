# Q410: Keeper.SetTxBloom - Failed Receipt Logs Removed But Bloom Still Signals Event

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `EVM log bloom persistence during ApplyTransaction` while controlling `duplicate hash scenario` and `tx hash`, under the precondition that multiple Ethereum messages appear in one Cosmos transaction, drive `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts` in `x/evm/keeper/bloom.go::Keeper.SetTxBloom` so that failed receipt logs removed but bloom still signals event, violating the invariant that failed transactions must not be represented as successful fund transfers, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/bloom.go::Keeper.SetTxBloom`
- Entrypoint: `EVM log bloom persistence during ApplyTransaction`
- Attacker controls: `duplicate hash scenario`, `tx hash`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: failed receipt logs removed but bloom still signals event through `FinalizeBlock events -> KVIndexer.IndexBlock -> GetTransactionReceipt/GetBlockReceipts`.
- Invariant to test: failed transactions must not be represented as successful fund transfers.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: replay the same scenario through eth_call or estimateGas and through eth_sendRawTransaction and assert the only difference is persistence.
