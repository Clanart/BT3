# Q3848: WombatStaking.withdraw - withdraw pays out a balance delta rather than a computed entitlement

## Question
wombat/WombatStaking.sol: withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. With _liquidity and _minAmount, forwarded verbatim from the helper's withdraw under attacker control and the pool is marked isPoolFeeFree so the fee loop is skipped entirely, can an unprivileged caller sequence `withdraw(address,uint256,uint256,address) via a pool helper` so that `isPoolFeeFree[_lpToken]` and `feeInfos.length` no longer reconcile, violating the invariant that a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: withdraw pays out a balance delta rather than a computed entitlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool is marked isPoolFeeFree so the fee loop is skipped entirely, call `withdraw(address,uint256,uint256,address) via a pool helper`, and assert `isPoolFeeFree[_lpToken]` equals `feeInfos.length` and that no account can withdraw more than it put in.
