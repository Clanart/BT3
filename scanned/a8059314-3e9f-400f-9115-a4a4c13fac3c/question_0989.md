# Q0989: SimplePoolHelper.depositFor - safeApprove without reset before the MasterMagpie deposit

## Question
In wombat/SimplePoolHelper.sol, depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Does `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` let an unprivileged caller exploit that under MasterMagpie is paused so depositFor reverts after the pull has already happened, so that `authorized[msg.sender]` diverges from `the beneficiary _for chosen by the authorized caller`, the invariant that an approval on a shared deposit path must be idempotent is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: safeApprove without reset before the MasterMagpie deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Precondition: MasterMagpie is paused so depositFor reverts after the pull has already happened.
- Invariant to test: an approval on a shared deposit path must be idempotent; concretely, `authorized[msg.sender]` must stay reconciled with `the beneficiary _for chosen by the authorized caller`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish MasterMagpie is paused so depositFor reverts after the pull has already happened, have the attacker run `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`, then assert the victim's claimable value and the `authorized[msg.sender]` versus `the beneficiary _for chosen by the authorized caller` relation are unchanged by the attacker's transaction.
