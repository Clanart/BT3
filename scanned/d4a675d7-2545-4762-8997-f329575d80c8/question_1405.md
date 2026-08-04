# Q1405: withdraw account resurrection

## Question
Can an unprivileged attacker reach `withdraw` by submit transactions invoking writable-account instructions with lamport amounts, account ownership transitions, cpi ordering, and close/reopen patterns such that a zero-lamport or closed account can be revived or reused incorrectly, breaking the invariant that closed or zero-lamport accounts must not resurrect without a valid recreation path and leading to `Loss of Funds`?

## Target
- File/function: runtime/src/bank.rs::withdraw
- Entrypoint: submit transactions invoking writable-account instructions
- Attacker controls: lamport amounts, account ownership transitions, CPI ordering, and close/reopen patterns
- Exploit idea: look for stale cache or store ordering that makes dead accounts look live again
- Invariant to test: closed or zero-lamport accounts must not resurrect without a valid recreation path
- Expected Immunefi impact: Loss of Funds
- Fast validation: close and recreate the same account shape repeatedly and diff live/dead visibility
