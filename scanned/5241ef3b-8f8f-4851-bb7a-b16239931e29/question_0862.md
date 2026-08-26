# Q0862: ArbWomUp.incentiveDeposit - the deposited WOM has no redemption path in this contract

## Question
In wombat/ArbWomUp.sol, the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. Starting from a state where the caller splits the same total deposit across many small calls, can an unprivileged EOA use `incentiveDeposit(uint256 _amount)` to leave `accumulated = _amount + userWOMDeposited[account]` inconsistent with `the tier boundary crossed`, violating the invariant that a one-way deposit must be matched by an entitlement that cannot be under-recorded and extracting Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the deposited WOM has no redemption path in this contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. Precondition: the caller splits the same total deposit across many small calls.
- Invariant to test: a one-way deposit must be matched by an entitlement that cannot be under-recorded; concretely, `accumulated = _amount + userWOMDeposited[account]` must stay reconciled with `the tier boundary crossed`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount)`: constrain the setup so that the caller splits the same total deposit across many small calls, fuzz the attacker inputs (_amount with no per-user or global cap, and how many times the call is repeated), and assert after every call that a one-way deposit must be matched by an entitlement that cannot be under-recorded.
