# Q1741: SmartWomConvert.depositFor - depositFor approves MasterMagpie without a reset

## Question
Consider wombat/SmartWomConvert.sol, where depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Assuming the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, can an unprivileged attacker turn this into a divergence between `_minRec` and `convertAmount + amountRec` via `depositFor(uint256 _amount, address _for)`, breaking the invariant that a permissionless deposit helper must not be blockable by allowance residue and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor approves MasterMagpie without a reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Precondition: the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs.
- Invariant to test: a permissionless deposit helper must not be blockable by allowance residue; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `depositFor(uint256 _amount, address _for)`: constrain the setup so that the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, fuzz the attacker inputs (_amount and _for, with the mWOM pulled from the caller), and assert after every call that a permissionless deposit helper must not be blockable by allowance residue.
