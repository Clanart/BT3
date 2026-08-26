# Q0924: ArbWomUp.incentiveDeposit - the reward is computed before the deposit is recorded

## Question
wombat/ArbWomUp.sol - incentiveDeposit() calls this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier accumulation and the deposit record are written from two different views of the same state. Can an unprivileged attacker controlling _amount with no per-user or global cap, and how many times the call is repeated, under the caller splits the same total deposit across several addresses, exploit this through `incentiveDeposit(uint256 _amount)` to break the reconciliation between `claimedReward[account]` and `userWOMDeposited[account]` and the invariant that the tier input and the deposit record must be derived from one snapshot, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the reward is computed before the deposit is recorded)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: incentiveDeposit() calls this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier accumulation and the deposit record are written from two different views of the same state. Precondition: the caller splits the same total deposit across several addresses.
- Invariant to test: the tier input and the deposit record must be derived from one snapshot; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount)` sequence atomically under the caller splits the same total deposit across several addresses, asserting at the end that `claimedReward[account]` still equals `userWOMDeposited[account]` and the PoC's balance delta is non-positive.
