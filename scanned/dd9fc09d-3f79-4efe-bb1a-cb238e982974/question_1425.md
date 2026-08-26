# Q1425: SimplePoolHelper.depositFor - safeApprove without reset before the MasterMagpie deposit

## Question
Consider wombat/SimplePoolHelper.sol, where depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Assuming the stake token has a transfer hook the attacker controls, can an unprivileged attacker turn this into a divergence between `IERC20(stakeToken).balanceOf(address(this))` and `the amount credited by IMasterMagpie.depositFor` via `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`, breaking the invariant that an approval on a shared deposit path must be idempotent and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: safeApprove without reset before the MasterMagpie deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Precondition: the stake token has a transfer hook the attacker controls.
- Invariant to test: an approval on a shared deposit path must be idempotent; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the stake token has a transfer hook the attacker controls, call `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`, and assert `IERC20(stakeToken).balanceOf(address(this))` equals `the amount credited by IMasterMagpie.depositFor` and that no account can withdraw more than it put in.
