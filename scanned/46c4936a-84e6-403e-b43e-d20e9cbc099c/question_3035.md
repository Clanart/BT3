# Q3035: SmartWomConvert.depositFor - depositFor approves MasterMagpie without a reset

## Question
In wombat/SmartWomConvert.sol, depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Can an unprivileged attacker reach this through `depositFor(uint256 _amount, address _for)` while the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, and drive `_convertRatio` out of agreement with `DENOMINATOR` - breaking the invariant that a permissionless deposit helper must not be blockable by allowance residue - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor approves MasterMagpie without a reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Precondition: the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn.
- Invariant to test: a permissionless deposit helper must not be blockable by allowance residue; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, then assert `_convertRatio` and `DENOMINATOR` end identical in both runs.
