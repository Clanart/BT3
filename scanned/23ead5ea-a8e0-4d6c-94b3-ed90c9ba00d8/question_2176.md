# Q2176: StateDB.SetCode - Code Written For Authority Survives Failed Value Transfer

## Question
Can an unprivileged attacker create, delegate, clear, or selfdestruct code through public EVM execution through `EVM code mutation during CREATE or EIP-7702 delegation` while controlling `journal snapshot` and `authority account`, under the precondition that the address collides with a preinstall/precompile-like address, drive `SetCode -> stateObject code hash update -> journal snapshot -> Commit` in `x/evm/statedb/statedb.go::StateDB.SetCode` so that code written for authority survives failed value transfer, violating the invariant that only authorized execution can install, clear, or persist account code, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/statedb/statedb.go::StateDB.SetCode`
- Entrypoint: `EVM code mutation during CREATE or EIP-7702 delegation`
- Attacker controls: `journal snapshot`, `authority account`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: code written for authority survives failed value transfer through `SetCode -> stateObject code hash update -> journal snapshot -> Commit`.
- Invariant to test: only authorized execution can install, clear, or persist account code.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: write a focused Go unit test around the target function and assert bank supply, sender balance, nonce, code hash, logs, and receipt status before and after.
