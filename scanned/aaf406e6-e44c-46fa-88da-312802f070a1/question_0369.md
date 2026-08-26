# Q0369: SimplePoolHelper.depositFor - safeApprove without reset before the MasterMagpie deposit

## Question
wombat/SimplePoolHelper.sol: depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. With _amount and _for, forwarded by mWOM when the caller uses convertAndStake under attacker control and the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, can an unprivileged caller sequence `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` so that `_amount pulled from the caller` and `the allowance granted to masterMagpie` no longer reconcile, violating the invariant that an approval on a shared deposit path must be idempotent and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: safeApprove without reset before the MasterMagpie deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Precondition: the call arrives from WomUp.migrate with the mWOM approved but not fully consumed.
- Invariant to test: an approval on a shared deposit path must be idempotent; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _for, forwarded by mWOM when the caller uses convertAndStake) under the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, asserting on every row that an approval on a shared deposit path must be idempotent.
