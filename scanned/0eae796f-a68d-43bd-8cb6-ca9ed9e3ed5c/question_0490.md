# Q0490: ArbWomUp.incentiveDeposit - the reward is computed before the deposit is recorded

## Question
Consider wombat/ArbWomUp.sol, where incentiveDeposit() calls this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier accumulation and the deposit record are written from two different views of the same state. Assuming the caller sizes _amount to cross several tier boundaries at once, can an unprivileged attacker turn this into a divergence between `rewardTier[i]` and `rewardMultiplier[i-1]` via `incentiveDeposit(uint256 _amount)`, breaking the invariant that the tier input and the deposit record must be derived from one snapshot and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the reward is computed before the deposit is recorded)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: incentiveDeposit() calls this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier accumulation and the deposit record are written from two different views of the same state. Precondition: the caller sizes _amount to cross several tier boundaries at once.
- Invariant to test: the tier input and the deposit record must be derived from one snapshot; concretely, `rewardTier[i]` must stay reconciled with `rewardMultiplier[i-1]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount)`: constrain the setup so that the caller sizes _amount to cross several tier boundaries at once, fuzz the attacker inputs (_amount with no per-user or global cap, and how many times the call is repeated), and assert after every call that the tier input and the deposit record must be derived from one snapshot.
