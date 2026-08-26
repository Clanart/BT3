# Q1140: ArbWomUp.incentiveDeposit - the reward is computed before the deposit is recorded

## Question
wombat/ArbWomUp.sol - incentiveDeposit() calls this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier accumulation and the deposit record are written from two different views of the same state. Can an unprivileged attacker controlling _amount with no per-user or global cap, and how many times the call is repeated, under userWOMDeposited is still zero for the caller, exploit this through `incentiveDeposit(uint256 _amount)` to break the reconciliation between `rewardAmount / DENOMINATOR` and `claimedReward[account]` and the invariant that the tier input and the deposit record must be derived from one snapshot, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the reward is computed before the deposit is recorded)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: incentiveDeposit() calls this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier accumulation and the deposit record are written from two different views of the same state. Precondition: userWOMDeposited is still zero for the caller.
- Invariant to test: the tier input and the deposit record must be derived from one snapshot; concretely, `rewardAmount / DENOMINATOR` must stay reconciled with `claimedReward[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount)` sequence atomically under userWOMDeposited is still zero for the caller, asserting at the end that `rewardAmount / DENOMINATOR` still equals `claimedReward[account]` and the PoC's balance delta is non-positive.
