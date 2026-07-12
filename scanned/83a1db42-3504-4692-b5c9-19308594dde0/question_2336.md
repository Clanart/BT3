# Q2336: journal.Revert - Refundchange Restores Refund Counter But Not Leftover Gas

## Question
Can an unprivileged attacker execute a contract that writes storage around a native-action boundary through `EVM snapshot/revert during nested calls` while controlling `storage key` and `origin value`, under the precondition that the same storage key is written by EVM code and a native action, drive `SetState -> dirtyStorage/originStorage tracking -> native write -> Commit conflict check` in `x/evm/statedb/journal.go::journal.Revert` so that refundChange restores refund counter but not leftover gas, violating the invariant that state conflicts must not become partial successful fund movement, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/statedb/journal.go::journal.Revert`
- Entrypoint: `EVM snapshot/revert during nested calls`
- Attacker controls: `storage key`, `origin value`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: refundChange restores refund counter but not leftover gas through `SetState -> dirtyStorage/originStorage tracking -> native write -> Commit conflict check`.
- Invariant to test: state conflicts must not become partial successful fund movement.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: add a fuzz case varying the attacker-controlled fields and differential-check against go-ethereum for tx validity, gas, nonce, and code/storage effects.
