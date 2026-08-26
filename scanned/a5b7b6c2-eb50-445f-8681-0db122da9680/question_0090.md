# Q0090: SimplePoolHelper.depositFor - residue left on the helper has no owner and no recovery

## Question
In wombat/SimplePoolHelper.sol, the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Starting from a state where the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, can an unprivileged EOA use `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` to leave `_amount pulled from the caller` inconsistent with `the allowance granted to masterMagpie`, violating the invariant that value left on a pass-through helper must be attributable and recoverable and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: residue left on the helper has no owner and no recovery)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: the contract never measures a balance delta and never sweeps, so any stakeToken left behind by a partial deposit sits with no owner and is only recoverable through the next call's approval residue. Precondition: the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself.
- Invariant to test: value left on a pass-through helper must be attributable and recoverable; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, snapshot `_amount pulled from the caller` and `the allowance granted to masterMagpie`, run the attacker's `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
