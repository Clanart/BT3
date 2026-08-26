# Q0461: ArbWomUp3.incentiveDeposit - the reward is computed against a pre-deposit balance while the deposit is credited first

## Question
Note that in wombat/ArbWomUp3.sol, incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender, _mode == 2) before calling _deposit, but _deposit mode 2 locks into mWomSV, so the tier input, the double-count correction and the resulting locked balance are three different views of one state. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under the caller sets _mode to 2 so the doubling applies and force `bracketRewarded` apart from `calDoubledCounted(account)`, breaking the invariant that the tier input and the correction that offsets it must be taken from one snapshot for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the reward is computed against a pre-deposit balance while the deposit is credited first)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender, _mode == 2) before calling _deposit, but _deposit mode 2 locks into mWomSV, so the tier input, the double-count correction and the resulting locked balance are three different views of one state. Precondition: the caller sets _mode to 2 so the doubling applies.
- Invariant to test: the tier input and the correction that offsets it must be taken from one snapshot; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sets _mode to 2 so the doubling applies, call `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, and assert `bracketRewarded` equals `calDoubledCounted(account)` and that no account can withdraw more than it put in.
