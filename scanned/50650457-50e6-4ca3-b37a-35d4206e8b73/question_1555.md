# Q1555: SimplePoolHelper.depositFor - safeApprove without reset before the MasterMagpie deposit

## Question
In wombat/SimplePoolHelper.sol, depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Starting from a state where the beneficiary passed is an address the funding caller does not control, can an unprivileged EOA use `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` to leave `IERC20(stakeToken).balanceOf(address(this))` inconsistent with `the amount credited by IMasterMagpie.depositFor`, violating the invariant that an approval on a shared deposit path must be idempotent and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: safeApprove without reset before the MasterMagpie deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Precondition: the beneficiary passed is an address the funding caller does not control.
- Invariant to test: an approval on a shared deposit path must be idempotent; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _for, forwarded by mWOM when the caller uses convertAndStake) under the beneficiary passed is an address the funding caller does not control, asserting on every row that an approval on a shared deposit path must be idempotent.
