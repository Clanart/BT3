# Q1143: SimplePoolHelper.depositFor - safeApprove without reset before the MasterMagpie deposit

## Question
In wombat/SimplePoolHelper.sol, depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Can an unprivileged attacker reach this through `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` while MasterMagpie is paused so depositFor reverts after the pull has already happened, and drive `_amount pulled from the caller` out of agreement with `the allowance granted to masterMagpie` - breaking the invariant that an approval on a shared deposit path must be idempotent - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: safeApprove without reset before the MasterMagpie deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Precondition: MasterMagpie is paused so depositFor reverts after the pull has already happened.
- Invariant to test: an approval on a shared deposit path must be idempotent; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish MasterMagpie is paused so depositFor reverts after the pull has already happened, have the attacker run `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`, then assert the victim's claimable value and the `_amount pulled from the caller` versus `the allowance granted to masterMagpie` relation are unchanged by the attacker's transaction.
