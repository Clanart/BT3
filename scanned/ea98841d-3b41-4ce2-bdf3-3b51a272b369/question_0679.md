# Q0679: SimplePoolHelper.depositFor - safeApprove without reset before the MasterMagpie deposit

## Question
wombat/SimplePoolHelper.sol: depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Under a residual stakeToken balance from an earlier partial deposit sits on the helper, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` that leaves `IERC20(stakeToken).balanceOf(address(this))` unreconciled with `the amount credited by IMasterMagpie.depositFor`, violates the invariant that an approval on a shared deposit path must be idempotent, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: safeApprove without reset before the MasterMagpie deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Precondition: a residual stakeToken balance from an earlier partial deposit sits on the helper.
- Invariant to test: an approval on a shared deposit path must be idempotent; concretely, `IERC20(stakeToken).balanceOf(address(this))` must stay reconciled with `the amount credited by IMasterMagpie.depositFor`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`: constrain the setup so that a residual stakeToken balance from an earlier partial deposit sits on the helper, fuzz the attacker inputs (_amount and _for, forwarded by mWOM when the caller uses convertAndStake), and assert after every call that an approval on a shared deposit path must be idempotent.
