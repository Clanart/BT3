# Q1173: SimplePoolHelper.depositFor - residue left on the helper has no owner and no recovery

## Question
wombat/SimplePoolHelper.sol: the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Under MasterMagpie is paused so depositFor reverts after the pull has already happened, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` that leaves `IERC20(stakeToken).balanceOf(address(this))` unreconciled with `the amount credited by IMasterMagpie.depositFor`, violates the invariant that value left on a pass-through helper must be attributable and recoverable, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: residue left on the helper has no owner and no recovery)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Precondition: MasterMagpie is paused so depositFor reverts after the pull has already happened.
- Invariant to test: value left on a pass-through helper must be attributable and recoverable; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange MasterMagpie is paused so depositFor reverts after the pull has already happened, call `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`, and assert `IERC20(stakeToken).balanceOf(address(this))` equals `the amount credited by IMasterMagpie.depositFor` and that no account can withdraw more than it put in.
