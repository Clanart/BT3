# Q1266: Starknet BridgeToken mint custody accounting diverges from wrapped supply at boundary values

## Question
Can an unprivileged attacker trigger `public settlement-side mint path reached from `fin_transfer`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `starknet/src/bridge_token.cairo::mint` violate `minted supply must only arise from one validated settlement event and must not survive a later rollback or replay edge case` in the `custody accounting diverges from wrapped supply` attack class because mints wrapped supply into the recipient account under control of the omni bridge becomes fragile at those edges?

## Target
- File/function: `starknet/src/bridge_token.cairo::mint`
- Entrypoint: `public settlement-side mint path reached from `fin_transfer``
- Attacker controls: recipient address, amount, and any receiver-side behavior after receiving bridged tokens
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: minted supply must only arise from one validated settlement event and must not survive a later rollback or replay edge case
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
