# Q666: NEAR callback gas budgeting burn or lock before irreversible state at boundary values

## Question
Can an unprivileged attacker trigger `public finalization and fast-transfer flows with user-controlled `msg`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::send_tokens gas splitting plus callback consumers` violate `callback gas budgeting must not let attacker-controlled message size or branching force a partial economic effect before the contract can safely resolve the outcome` in the `burn or lock before irreversible state` attack class because computes `ft_transfer_call` gas from prepaid minus used gas and falls back to strict minimum checks before minting or transferring becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens gas splitting plus callback consumers`
- Entrypoint: `public finalization and fast-transfer flows with user-controlled `msg``
- Attacker controls: message length, gas left at call time, and whether the path chooses `ft_transfer` or `ft_transfer_call`
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: callback gas budgeting must not let attacker-controlled message size or branching force a partial economic effect before the contract can safely resolve the outcome
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
