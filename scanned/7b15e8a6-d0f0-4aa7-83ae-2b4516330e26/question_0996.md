# Q996: HookedStateDB.SetCode - Oncodechange Sees Nil Code While Bank State Persists

## Question
Can an unprivileged attacker request debug_trace* for crafted included transactions or calls through `debug/trace hooked EIP-7702 or CREATE code mutation` while controlling `block context` and `baseFee`, under the precondition that the traced transaction has predecessors in the same block, drive `debug_trace* -> predecessor replay -> ApplyMessageWithConfig in trace context -> result marshaling` in `x/evm/statedb/statedb_hooked.go::HookedStateDB.SetCode` so that OnCodeChange sees nil code while bank state persists, violating the invariant that traceReplay must not mask balance/fee invariants, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/statedb/statedb_hooked.go::HookedStateDB.SetCode`
- Entrypoint: `debug/trace hooked EIP-7702 or CREATE code mutation`
- Attacker controls: `block context`, `baseFee`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: OnCodeChange sees nil code while bank state persists through `debug_trace* -> predecessor replay -> ApplyMessageWithConfig in trace context -> result marshaling`.
- Invariant to test: traceReplay must not mask balance/fee invariants.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: force the edge value at uint64/uint256/sdk.Int boundaries and assert no smaller debit, larger refund, or supply change occurs.
