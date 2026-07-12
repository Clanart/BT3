# Q1183: Backend.GetLogs - Block Range Returns Logs Whose Receipt Status Is Failed

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `eth_getLogs or filter log retrieval` while controlling `duplicate hash scenario` and `gas used`, under the precondition that multiple Ethereum messages appear in one Cosmos transaction, drive `ApplyTransaction receipt/log output -> bloom/indexer storage -> public receipt/log query` in `rpc/backend/filters.go::Backend.GetLogs` so that block range returns logs whose receipt status is failed, violating the invariant that indexed accounting must match direct block reconstruction, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/filters.go::Backend.GetLogs`
- Entrypoint: `eth_getLogs or filter log retrieval`
- Attacker controls: `duplicate hash scenario`, `gas used`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: block range returns logs whose receipt status is failed through `ApplyTransaction receipt/log output -> bloom/indexer storage -> public receipt/log query`.
- Invariant to test: indexed accounting must match direct block reconstruction.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: build a two-message Cosmos tx fixture and assert ante, execution, refund, and receipt invariants after FinalizeBlock.
