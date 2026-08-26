# Q0707: ArbWomUp.incentiveDeposit - the reward is computed before the deposit is recorded

## Question
In wombat/ArbWomUp.sol, incentiveDeposit() calls this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier accumulation and the deposit record are written from two different views of the same state. Starting from a state where the caller splits the same total deposit across many small calls, can an unprivileged EOA use `incentiveDeposit(uint256 _amount)` to leave `accumulated = _amount + userWOMDeposited[account]` inconsistent with `the tier boundary crossed`, violating the invariant that the tier input and the deposit record must be derived from one snapshot and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the reward is computed before the deposit is recorded)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: incentiveDeposit() calls this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier accumulation and the deposit record are written from two different views of the same state. Precondition: the caller splits the same total deposit across many small calls.
- Invariant to test: the tier input and the deposit record must be derived from one snapshot; concretely, `accumulated = _amount + userWOMDeposited[account]` must stay reconciled with `the tier boundary crossed`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller splits the same total deposit across many small calls, then assert `accumulated = _amount + userWOMDeposited[account]` and `the tier boundary crossed` end identical in both runs.
