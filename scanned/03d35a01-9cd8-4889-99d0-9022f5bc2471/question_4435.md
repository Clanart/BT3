# Q4435: WombatPoolHelperV2.withdraw - _minAmount is caller-supplied on a shared Wombat withdrawal

## Question
Consider wombat/WombatPoolHelperV2.sol, where withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Assuming the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, can an unprivileged attacker turn this into a divergence between `this.balance(msg.sender)` and `lockedAmount[msg.sender]` via `withdraw(uint256 _liquidity, uint256 _minAmount)`, breaking the invariant that the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: _minAmount is caller-supplied on a shared Wombat withdrawal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount) under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, asserting on every row that the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller.
