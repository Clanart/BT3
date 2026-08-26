# Q0400: SimplePoolHelper.depositFor - residue left on the helper has no owner and no recovery

## Question
wombat/SimplePoolHelper.sol: the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. With _amount and _for, forwarded by mWOM when the caller uses convertAndStake under attacker control and the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, can an unprivileged caller sequence `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` so that `IERC20(stakeToken).balanceOf(address(this))` and `the amount credited by IMasterMagpie.depositFor` no longer reconcile, violating the invariant that value left on a pass-through helper must be attributable and recoverable and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: residue left on the helper has no owner and no recovery)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Precondition: the call arrives from WomUp.migrate with the mWOM approved but not fully consumed.
- Invariant to test: value left on a pass-through helper must be attributable and recoverable; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, then assert `IERC20(stakeToken).balanceOf(address(this))` and `the amount credited by IMasterMagpie.depositFor` end identical in both runs.
