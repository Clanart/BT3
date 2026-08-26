# Q5418: WombatStaking.withdraw - withdraw pays out a balance delta rather than a computed entitlement

## Question
In wombat/WombatStaking.sol, withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Starting from a state where the bonus reward token registered for the asset is also one of the fee currencies, can an unprivileged EOA use `withdraw(address,uint256,uint256,address) via a pool helper` to leave `IERC20(wom).balanceOf(address(this))` inconsistent with `totalConverted in mWOM`, violating the invariant that a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: withdraw pays out a balance delta rather than a computed entitlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the bonus reward token registered for the asset is also one of the fee currencies, call `withdraw(address,uint256,uint256,address) via a pool helper`, and assert `IERC20(wom).balanceOf(address(this))` equals `totalConverted in mWOM` and that no account can withdraw more than it put in.
