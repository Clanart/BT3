# Q0524: SimplePoolHelper.depositFor - safeApprove without reset before the MasterMagpie deposit

## Question
In wombat/SimplePoolHelper.sol, depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Does `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` let an unprivileged caller exploit that under the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, so that `IERC20(stakeToken).balanceOf(address(this))` diverges from `the amount credited by IMasterMagpie.depositFor`, the invariant that an approval on a shared deposit path must be idempotent is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: safeApprove without reset before the MasterMagpie deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Precondition: the call arrives from WomUp.migrate with the mWOM approved but not fully consumed.
- Invariant to test: an approval on a shared deposit path must be idempotent; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the call arrives from WomUp.migrate with the mWOM approved but not fully consumed, snapshot `IERC20(stakeToken).balanceOf(address(this))` and `the amount credited by IMasterMagpie.depositFor`, run the attacker's `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
