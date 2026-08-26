# Q4019: SmartWomConvert.depositFor - depositFor approves MasterMagpie without a reset

## Question
In wombat/SmartWomConvert.sol, depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Can an unprivileged attacker reach this through `depositFor(uint256 _amount, address _for)` while the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, and drive `maxSwapAmount()` out of agreement with `IAsset(womAsset).cash() and IAsset(womAsset).liability()` - breaking the invariant that a permissionless deposit helper must not be blockable by allowance residue - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor approves MasterMagpie without a reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: a permissionless deposit helper must not be blockable by allowance residue; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `depositFor(uint256 _amount, address _for)`: constrain the setup so that the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, fuzz the attacker inputs (_amount and _for, with the mWOM pulled from the caller), and assert after every call that a permissionless deposit helper must not be blockable by allowance residue.
