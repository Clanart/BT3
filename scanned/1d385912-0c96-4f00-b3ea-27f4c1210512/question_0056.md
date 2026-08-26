# Q0056: ArbWomUp.incentiveDeposit - the reward is computed before the deposit is recorded

## Question
In wombat/ArbWomUp.sol, incentiveDeposit() calls this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier accumulation and the deposit record are written from two different views of the same state. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount)` while the contract's USDT balance is below the tier reward the deposit earned, and drive `rewardAmount / DENOMINATOR` out of agreement with `claimedReward[account]` - breaking the invariant that the tier input and the deposit record must be derived from one snapshot - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the reward is computed before the deposit is recorded)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: incentiveDeposit() calls this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier accumulation and the deposit record are written from two different views of the same state. Precondition: the contract's USDT balance is below the tier reward the deposit earned.
- Invariant to test: the tier input and the deposit record must be derived from one snapshot; concretely, `rewardAmount / DENOMINATOR` must stay reconciled with `claimedReward[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the contract's USDT balance is below the tier reward the deposit earned, snapshot `rewardAmount / DENOMINATOR` and `claimedReward[account]`, run the attacker's `incentiveDeposit(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
