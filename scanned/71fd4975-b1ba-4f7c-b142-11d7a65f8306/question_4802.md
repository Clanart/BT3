# Q4802: SmartWomConvert.depositFor - depositFor approves MasterMagpie without a reset

## Question
wombat/SmartWomConvert.sol: depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Under the attacker sandwiches the transaction on the wom/mWom Wombat pool, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for)` that leaves `_minRec` unreconciled with `convertAmount + amountRec`, violates the invariant that a permissionless deposit helper must not be blockable by allowance residue, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor approves MasterMagpie without a reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: a permissionless deposit helper must not be blockable by allowance residue; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker sandwiches the transaction on the wom/mWom Wombat pool, then assert `_minRec` and `convertAmount + amountRec` end identical in both runs.
