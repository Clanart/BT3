# Q1288: SimplePoolHelper.depositFor - safeApprove without reset before the MasterMagpie deposit

## Question
In wombat/SimplePoolHelper.sol, depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Can an unprivileged attacker reach this through `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` while the stake token has a transfer hook the attacker controls, and drive `_amount pulled from the caller` out of agreement with `the allowance granted to masterMagpie` - breaking the invariant that an approval on a shared deposit path must be idempotent - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` (mechanism: safeApprove without reset before the MasterMagpie deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by mWOM when the caller uses convertAndStake
- Exploit idea: depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Precondition: the stake token has a transfer hook the attacker controls.
- Invariant to test: an approval on a shared deposit path must be idempotent; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `depositFor(uint256 _amount, address _for) reached through mWOM.convertAndStake` sequence atomically under the stake token has a transfer hook the attacker controls, asserting at the end that `_amount pulled from the caller` still equals `the allowance granted to masterMagpie` and the PoC's balance delta is non-positive.
