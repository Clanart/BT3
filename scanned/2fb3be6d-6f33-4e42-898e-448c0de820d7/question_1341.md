# Q1341: ArbWomUp.incentiveDeposit - the reward is computed before the deposit is recorded

## Question
In wombat/ArbWomUp.sol, incentiveDeposit() calls this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier accumulation and the deposit record are written from two different views of the same state. Does `incentiveDeposit(uint256 _amount)` let an unprivileged caller exploit that under the caller has already claimed most of their tier entitlement, so that `usdtReward` diverges from `IERC20(usdt).balanceOf(address(this))`, the invariant that the tier input and the deposit record must be derived from one snapshot is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the reward is computed before the deposit is recorded)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: incentiveDeposit() calls this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier accumulation and the deposit record are written from two different views of the same state. Precondition: the caller has already claimed most of their tier entitlement.
- Invariant to test: the tier input and the deposit record must be derived from one snapshot; concretely, `usdtReward` must stay reconciled with `IERC20(usdt).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller has already claimed most of their tier entitlement, then assert `usdtReward` and `IERC20(usdt).balanceOf(address(this))` end identical in both runs.
