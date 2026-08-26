# Q5321: SmartWomConvert.depositFor - depositFor approves MasterMagpie without a reset

## Question
Note that in wombat/SmartWomConvert.sol, depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Can an attacker holding only tokens bought on market reach it via `depositFor(uint256 _amount, address _for)` under _convertRatio is set to zero so the entire input goes through the AMM and force `_convertRatio` apart from `DENOMINATOR`, breaking the invariant that a permissionless deposit helper must not be blockable by allowance residue for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor approves MasterMagpie without a reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Precondition: _convertRatio is set to zero so the entire input goes through the AMM.
- Invariant to test: a permissionless deposit helper must not be blockable by allowance residue; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _for, with the mWOM pulled from the caller) under _convertRatio is set to zero so the entire input goes through the AMM, asserting on every row that a permissionless deposit helper must not be blockable by allowance residue.
