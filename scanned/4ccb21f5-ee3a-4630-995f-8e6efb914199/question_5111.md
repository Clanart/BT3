# Q5111: SmartWomConvert.depositFor - depositFor approves MasterMagpie without a reset

## Question
wombat/SmartWomConvert.sol - depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Can an unprivileged attacker controlling _amount and _for, with the mWOM pulled from the caller, under the router leaves a non-zero allowance after the swap, exploit this through `depositFor(uint256 _amount, address _for)` to break the reconciliation between `obtainedmWomAmount` and `IERC20(mWom).balanceOf(address(this))` and the invariant that a permissionless deposit helper must not be blockable by allowance residue, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor approves MasterMagpie without a reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: a permissionless deposit helper must not be blockable by allowance residue; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the router leaves a non-zero allowance after the swap, have the attacker run `depositFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `obtainedmWomAmount` versus `IERC20(mWom).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
