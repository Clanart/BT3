# Q218: journal.Revert - Refundchange Restores Refund Counter But Not Leftover Gas

## Question
Can an unprivileged attacker execute a contract that writes storage around a native-action boundary through `EVM snapshot/revert during nested calls` while controlling `nested call revert` and `origin value`, under the precondition that a nested call reverts after writing storage, drive `Snapshot -> nested storage/native writes -> RevertToSnapshot -> final Commit` in `x/evm/statedb/journal.go::journal.Revert` so that refundChange restores refund counter but not leftover gas, violating the invariant that zero-value deletion must match geth storage semantics, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/statedb/journal.go::journal.Revert`
- Entrypoint: `EVM snapshot/revert during nested calls`
- Attacker controls: `nested call revert`, `origin value`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: refundChange restores refund counter but not leftover gas through `Snapshot -> nested storage/native writes -> RevertToSnapshot -> final Commit`.
- Invariant to test: zero-value deletion must match geth storage semantics.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: build a two-message Cosmos tx fixture and assert ante, execution, refund, and receipt invariants after FinalizeBlock.
