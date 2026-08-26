# Q1563: WombatStaking.withdraw - withdraw pays out a balance delta rather than a computed entitlement

## Question
wombat/WombatStaking.sol - withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Can an unprivileged attacker controlling _liquidity and _minAmount, forwarded verbatim from the helper's withdraw, under the contract is holding WOM collected as a protocol fee that has not yet been split, exploit this through `withdraw(address,uint256,uint256,address) via a pool helper` to break the reconciliation between `totalAccumulated in mWOM` and `veWom balance of WombatStaking` and the invariant that a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: withdraw pays out a balance delta rather than a computed entitlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract is holding WOM collected as a protocol fee that has not yet been split, call `withdraw(address,uint256,uint256,address) via a pool helper`, and assert `totalAccumulated in mWOM` equals `veWom balance of WombatStaking` and that no account can withdraw more than it put in.
