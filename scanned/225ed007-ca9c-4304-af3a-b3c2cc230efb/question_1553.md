# Q1553: ArbWomUp2.incentiveDeposit - the reward is computed before the deposit is recorded

## Question
In wombat/ArbWomUp2.sol, incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. Starting from a state where the router pair for the bull swap holds thin liquidity, can an unprivileged EOA use `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` to leave `calDoubledCounted(account)` inconsistent with `rewardTier and rewardMultiplier walk`, violating the invariant that the tier input and the deposit record must be derived from one snapshot and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the reward is computed before the deposit is recorded)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. Precondition: the router pair for the bull swap holds thin liquidity.
- Invariant to test: the tier input and the deposit record must be derived from one snapshot; concretely, `calDoubledCounted(account)` must stay reconciled with `rewardTier and rewardMultiplier walk`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the router pair for the bull swap holds thin liquidity, snapshot `calDoubledCounted(account)` and `rewardTier and rewardMultiplier walk`, run the attacker's `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
