# Q4345: AnkrBNBPoolHelper.withdraw - balance() and totalStaked() read a different ledger than the payout

## Question
In wombat/AnkrBNBPoolHelper.sol, balance(address) and totalStaked() read MasterMagpie stakingInfo and the receipt token supply, while withdraw pays a Wombat deposit-token balance delta, so the accounting a user sees and the value they receive come from unrelated sources. Does `withdraw(uint256 _liquidity, uint256 _minAmount)` let an unprivileged caller exploit that under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, so that `_liquidity burned via burnReceiptToken` diverges from `the deposit-token balance delta paid out by WombatStaking.withdraw`, the invariant that the balance a user is shown must be the exact basis on which their withdrawal is priced is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: balance() and totalStaked() read a different ledger than the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: balance(address) and totalStaked() read MasterMagpie stakingInfo and the receipt token supply, while withdraw pays a Wombat deposit-token balance delta, so the accounting a user sees and the value they receive come from unrelated sources. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: the balance a user is shown must be the exact basis on which their withdrawal is priced; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity, _minAmount and the ordering against the lockedAmount check) under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, asserting on every row that the balance a user is shown must be the exact basis on which their withdrawal is priced.
