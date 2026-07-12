# Q1632: Keeper.GetNonce - Pending Nonce And Committed Nonce Diverge Under Strict Mempool

## Question
Can an unprivileged attacker submit replay, reorder, or replacement transactions from attacker-controlled accounts through `nonce read for RPC, ante, and EVM execution` while controlling `replay timing` and `sender nonce`, under the precondition that contract creation performs nested CREATE operations, drive `SetCode authorization nonce bump -> tx sender nonce handling -> StateDB.Commit` in `x/evm/keeper/keeper.go::Keeper.GetNonce` so that pending nonce and committed nonce diverge under strict mempool, violating the invariant that failed paths must not create replayable nonce gaps or stale nonces, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/keeper.go::Keeper.GetNonce`
- Entrypoint: `nonce read for RPC, ante, and EVM execution`
- Attacker controls: `replay timing`, `sender nonce`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: pending nonce and committed nonce diverge under strict mempool through `SetCode authorization nonce bump -> tx sender nonce handling -> StateDB.Commit`.
- Invariant to test: failed paths must not create replayable nonce gaps or stale nonces.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: run a local integration test that submits the crafted raw tx through JSON-RPC and compares committed state with direct keeper queries.
