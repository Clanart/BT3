# Q4319: AnkrBNBPoolHelper.withdraw - _minAmount is caller-supplied on a shared Wombat withdrawal

## Question
In wombat/AnkrBNBPoolHelper.sol, withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Does `withdraw(uint256 _liquidity, uint256 _minAmount)` let an unprivileged caller exploit that under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, so that `_liquidity burned via burnReceiptToken` diverges from `the deposit-token balance delta paid out by WombatStaking.withdraw`, the invariant that the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: _minAmount is caller-supplied on a shared Wombat withdrawal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `withdraw(uint256 _liquidity, uint256 _minAmount)`: constrain the setup so that the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, fuzz the attacker inputs (_liquidity, _minAmount and the ordering against the lockedAmount check), and assert after every call that the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller.
