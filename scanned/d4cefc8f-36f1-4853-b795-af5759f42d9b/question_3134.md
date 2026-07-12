# Q3134: Backend.GetBlockReceipts - Receipt Bloom Logs Include Reverted Hook Logs

## Question
Can an unprivileged attacker query protocol receipts or logs for crafted included Ethereum transactions through `eth_getBlockReceipts public JSON-RPC` while controlling `receipt status` and `tx result events`, under the precondition that a Cronos-controlled accounting path consumes protocol receipt/log data, drive `block-scoped receipt rebuild -> TxResult lookup -> Cronos-controlled accounting consumer` in `rpc/backend/blocks.go::Backend.GetBlockReceipts` so that receipt bloom/logs include reverted hook logs, violating the invariant that duplicate hashes or mixed messages must not overwrite receipt identity, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `rpc/backend/blocks.go::Backend.GetBlockReceipts`
- Entrypoint: `eth_getBlockReceipts public JSON-RPC`
- Attacker controls: `receipt status`, `tx result events`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: receipt bloom/logs include reverted hook logs through `block-scoped receipt rebuild -> TxResult lookup -> Cronos-controlled accounting consumer`.
- Invariant to test: duplicate hashes or mixed messages must not overwrite receipt identity.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: add a fuzz case varying the attacker-controlled fields and differential-check against go-ethereum for tx validity, gas, nonce, and code/storage effects.
