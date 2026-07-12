# Q1504: StateDB.SetCode - Journal Revert Restores Code But Not Account Code Hash

## Question
Can an unprivileged attacker create, delegate, clear, or selfdestruct code through public EVM execution through `EVM code mutation during CREATE or EIP-7702 delegation` while controlling `journal snapshot` and `authority account`, under the precondition that the address collides with a preinstall/precompile-like address, drive `SELFDESTRUCT/recreate -> Finalise -> DeleteAccount -> code lookup` in `x/evm/statedb/statedb.go::StateDB.SetCode` so that journal revert restores code but not account code hash, violating the invariant that only authorized execution can install, clear, or persist account code, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/statedb/statedb.go::StateDB.SetCode`
- Entrypoint: `EVM code mutation during CREATE or EIP-7702 delegation`
- Attacker controls: `journal snapshot`, `authority account`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: journal revert restores code but not account code hash through `SELFDESTRUCT/recreate -> Finalise -> DeleteAccount -> code lookup`.
- Invariant to test: only authorized execution can install, clear, or persist account code.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: write a focused Go unit test around the target function and assert bank supply, sender balance, nonce, code hash, logs, and receipt status before and after.
