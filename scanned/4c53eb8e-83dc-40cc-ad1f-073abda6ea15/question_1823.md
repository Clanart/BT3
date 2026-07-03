# Q1823: check_is_reverted proof-fact binding gap in execution/execution_constraints__virtual.cairo (boundary-value edge)

## Question
Can a normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts use proof_facts payload to make `check_is_reverted` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints__virtual.cairo` accept attacker-supplied proof_facts that are valid under one base block/config but are consumed as if they authorize another state or block context around proof-fact acceptance, so that honest StarkNet OS execution commits, emits, or accepts an attacker-favorable result that reaches permanent freezing of funds? Specifically, can boundary field values, empty/non-empty transitions, or zero-special-case branches trigger the discrepancy?

## Target
- File/function: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints__virtual.cairo:7 :: check_is_reverted
- Entrypoint: normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts
- Attacker controls: proof_facts payload
- Exploit idea: break the binding between the invoke transaction, virtual OS header, stored block hash, and OS config hash while this function is handling proof-fact acceptance. Use attacker-chosen boundary values, zero sentinels, and empty/non-empty transitions to hit the sharp branch in this path.
- Invariant to test: proof-backed transactions must only be accepted when the proof header, stored base block hash, and OS config hash all bind to the same authorized context Boundary values and zero-special-case handling must preserve the same security and accounting invariant as ordinary values.
- Expected bounty impact: Permanent freezing of funds
- Fast validation: craft proof_facts around boundary block numbers, alternate program hashes, and stale config hashes through this function, then assert no accepted proof can bind to the wrong base block or config Add a focused fuzz harness around zero, max-sized, empty, and single-element cases and assert the same invariant still holds.
