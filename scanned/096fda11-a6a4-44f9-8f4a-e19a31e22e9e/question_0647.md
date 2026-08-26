# Q0647: ArbWomUp3.incentiveDeposit - the incentive pot is drainable by one oversized deposit

## Question
Note that in wombat/ArbWomUp3.sol, the tier walk has no per-transaction ceiling, and with the mode two doubling the payout can reach and exceed the whole MGP balance in one call. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under the caller sets _mode to 2 so the doubling applies and force `bracketRewarded` apart from `calDoubledCounted(account)`, breaking the invariant that an incentive pot must not be fully claimable by a single actor in one transaction for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the incentive pot is drainable by one oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: the tier walk has no per-transaction ceiling, and with the mode two doubling the payout can reach and exceed the whole MGP balance in one call. Precondition: the caller sets _mode to 2 so the doubling applies.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence atomically under the caller sets _mode to 2 so the doubling applies, asserting at the end that `bracketRewarded` still equals `calDoubledCounted(account)` and the PoC's balance delta is non-positive.
