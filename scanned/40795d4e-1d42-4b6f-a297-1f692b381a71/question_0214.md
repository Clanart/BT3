# Q0214: SimplePoolHelper.depositFor - safeApprove without reset before the MasterMagpie deposit

## Question
wombat/SimplePoolHelper.sol: depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Under the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` that leaves `_amount pulled from the caller` unreconciled with `the allowance granted to masterMagpie`, violates the invariant that an approval on a shared deposit path must be idempotent, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/SimplePoolHelper.sol -> `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` (mechanism: safeApprove without reset before the MasterMagpie deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for) reached through WomUp.migrate`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, forwarded by WomUp when the caller migrates
- Exploit idea: depositFor() calls IERC20(stakeToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so a depositFor that under-consumes leaves residue that permanently disables every route into this helper. Precondition: the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself.
- Invariant to test: an approval on a shared deposit path must be idempotent; concretely, `_amount pulled from the caller` must stay reconciled with `the allowance granted to masterMagpie`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `depositFor(uint256 _amount, address _for) reached through WomUp.migrate` sequence atomically under the call arrives from mWOM.convertAndStake with the mWOM minted to mWOM itself, asserting at the end that `_amount pulled from the caller` still equals `the allowance granted to masterMagpie` and the PoC's balance delta is non-positive.
