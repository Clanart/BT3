# Q1316: SimplePoolHelper.depositFor - residue left on the helper has no owner and no recovery

## Question
In wombat/SimplePoolHelper.sol, the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Does `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` let an unprivileged caller exploit that under the stake token has a transfer hook the attacker controls, so that `IERC20(stakeToken).balanceOf(address(this))` diverges from `the amount credited by IMasterMagpie.depositFor`, the invariant that value left on a pass-through helper must be attributable and recoverable is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: residue left on the helper has no owner and no recovery)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Precondition: the stake token has a transfer hook the attacker controls.
- Invariant to test: value left on a pass-through helper must be attributable and recoverable; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the stake token has a transfer hook the attacker controls, have the attacker run `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`, then assert the victim's claimable value and the `IERC20(stakeToken).balanceOf(address(this))` versus `the amount credited by IMasterMagpie.depositFor` relation are unchanged by the attacker's transaction.
