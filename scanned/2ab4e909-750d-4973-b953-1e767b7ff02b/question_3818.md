# Q3818: journal.Revert - Addlogchange Truncates Logs After Receipt Already Built

## Question
Can an unprivileged attacker execute a contract that writes storage around a native-action boundary through `EVM snapshot/revert during nested calls` while controlling `origin value` and `nested call revert`, under the precondition that a nested call reverts after writing storage, drive `journal.Revert -> stateObject restore -> keeper storage comparison` in `x/evm/statedb/journal.go::journal.Revert` so that addLogChange truncates logs after receipt already built, violating the invariant that zero-value deletion must match geth storage semantics, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/statedb/journal.go::journal.Revert`
- Entrypoint: `EVM snapshot/revert during nested calls`
- Attacker controls: `origin value`, `nested call revert`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: addLogChange truncates logs after receipt already built through `journal.Revert -> stateObject restore -> keeper storage comparison`.
- Invariant to test: zero-value deletion must match geth storage semantics.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: compare IndexBlock/GetTransactionReceipt/GetBlockReceipts output against direct block/result reconstruction for the same transaction.
