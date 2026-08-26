# Q1020: SimplePoolHelper.depositFor - residue left on the helper has no owner and no recovery

## Question
In wombat/SimplePoolHelper.sol, the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Does `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` let an unprivileged caller exploit that under MasterMagpie is paused so depositFor reverts after the pull has already happened, so that `_amount pulled from the caller` diverges from `the allowance granted to masterMagpie`, the invariant that value left on a pass-through helper must be attributable and recoverable is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: residue left on the helper has no owner and no recovery)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Precondition: MasterMagpie is paused so depositFor reverts after the pull has already happened.
- Invariant to test: value left on a pass-through helper must be attributable and recoverable; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`: constrain the setup so that MasterMagpie is paused so depositFor reverts after the pull has already happened, fuzz the attacker inputs (_amount and _for, forwarded by mWOM when the caller uses convertAndStake), and assert after every call that value left on a pass-through helper must be attributable and recoverable.
