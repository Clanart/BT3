# Q2801: ArbWomUp3.incentiveDeposit - an unrecognised mode silently takes the plain transfer branch

## Question
Consider wombat/ArbWomUp3.sol, where _deposit() treats mode 1 as a SmartWomConvert deposit, mode 2 as the swap-and-lock path and anything else as a plain mWOM transfer, so an unexpected mode value falls through to the least restrictive settlement while the reward was priced for a different one. Assuming the caller crosses several tier boundaries in one deposit, can an unprivileged attacker turn this into a divergence between `mWomSV.getUserTotalLocked(account) read by getRewardAmount` and `the same read inside calDoubledCounted` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that an unrecognised routing mode must revert rather than settle on the least restrictive branch and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: an unrecognised mode silently takes the plain transfer branch)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() treats mode 1 as a SmartWomConvert deposit, mode 2 as the swap-and-lock path and anything else as a plain mWOM transfer, so an unexpected mode value falls through to the least restrictive settlement while the reward was priced for a different one. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: an unrecognised routing mode must revert rather than settle on the least restrictive branch; concretely, `mWomSV.getUserTotalLocked(account) read by getRewardAmount` must stay reconciled with `the same read inside calDoubledCounted`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence atomically under the caller crosses several tier boundaries in one deposit, asserting at the end that `mWomSV.getUserTotalLocked(account) read by getRewardAmount` still equals `the same read inside calDoubledCounted` and the PoC's balance delta is non-positive.
