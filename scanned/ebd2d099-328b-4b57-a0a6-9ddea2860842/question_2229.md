# Q2229: AnkrBNBPoolHelper.withdraw - _minAmount is caller-supplied on a shared Wombat withdrawal

## Question
In wombat/AnkrBNBPoolHelper.sol, withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Starting from a state where the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, can an unprivileged EOA use `withdraw(uint256 _liquidity, uint256 _minAmount)` to leave `IERC20(stakingToken).totalSupply()` inconsistent with `the MasterWombat staked balance for pid`, violating the invariant that the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: _minAmount is caller-supplied on a shared Wombat withdrawal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, call `withdraw(uint256 _liquidity, uint256 _minAmount)`, and assert `IERC20(stakingToken).totalSupply()` equals `the MasterWombat staked balance for pid` and that no account can withdraw more than it put in.
