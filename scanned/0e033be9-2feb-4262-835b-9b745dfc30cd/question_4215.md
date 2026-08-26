# Q4215: AnkrBNBPoolHelper.depositNative - receipt-token delta credited to an attacker-chosen beneficiary

## Question
In wombat/AnkrBNBPoolHelper.sol, _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Can an unprivileged attacker reach this through `depositNative(uint256 _minimumLiquidity)` while the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, and drive `this.balance(msg.sender)` out of agreement with `lockedAmount[msg.sender]` - breaking the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (msg.value and _minimumLiquidity) under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, asserting on every row that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution.
