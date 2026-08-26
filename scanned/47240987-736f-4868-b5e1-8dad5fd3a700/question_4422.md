# Q4422: WombatPoolHelperV2.withdraw - burnReceiptToken is the last step and is not atomic with the payout

## Question
wombat/WombatPoolHelperV2.sol - withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Can an unprivileged attacker controlling _liquidity and _minAmount, under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, exploit this through `withdraw(uint256 _liquidity, uint256 _minAmount)` to break the reconciliation between `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` and the invariant that receipt supply must fall in the same step as the backing it represents, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: burnReceiptToken is the last step and is not atomic with the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: receipt supply must fall in the same step as the backing it represents; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount) under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, asserting on every row that receipt supply must fall in the same step as the backing it represents.
