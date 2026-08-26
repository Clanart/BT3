# Q2434: WombatPoolHelper.withdraw - _minAmount is caller-supplied on a shared Wombat withdrawal

## Question
In wombat/WombatPoolHelper.sol, withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Can an unprivileged attacker reach this through `withdraw(uint256 _liquidity, uint256 _minAmount)` while the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, and drive `IERC20(stakingToken).totalSupply()` out of agreement with `the MasterWombat staked balance for pid` - breaking the invariant that the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: _minAmount is caller-supplied on a shared Wombat withdrawal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, then assert `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` end identical in both runs.
