# Q0710: SimplePoolHelper.depositFor - residue left on the helper has no owner and no recovery

## Question
wombat/SimplePoolHelper.sol: the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Under a residual stakeToken balance from an earlier partial deposit sits on the helper, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` that leaves `authorized[msg.sender]` unreconciled with `the beneficiary _for chosen by the authorized caller`, violates the invariant that value left on a pass-through helper must be attributable and recoverable, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: residue left on the helper has no owner and no recovery)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Precondition: a residual stakeToken balance from an earlier partial deposit sits on the helper.
- Invariant to test: value left on a pass-through helper must be attributable and recoverable; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _for, forwarded by mWOM when the caller uses convertAndStake) under a residual stakeToken balance from an earlier partial deposit sits on the helper, asserting on every row that value left on a pass-through helper must be attributable and recoverable.
