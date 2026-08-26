# Q3546: SmartWomConvert.depositFor - depositFor approves MasterMagpie without a reset

## Question
wombat/SmartWomConvert.sol: depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Under the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for)` that leaves `currentRatio()` unreconciled with `buybackThreshold`, violates the invariant that a permissionless deposit helper must not be blockable by allowance residue, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor approves MasterMagpie without a reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: a permissionless deposit helper must not be blockable by allowance residue; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `depositFor(uint256 _amount, address _for)` sequence atomically under the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, asserting at the end that `currentRatio()` still equals `buybackThreshold` and the PoC's balance delta is non-positive.
