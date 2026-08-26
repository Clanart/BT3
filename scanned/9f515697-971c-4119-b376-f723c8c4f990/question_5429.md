# Q5429: AnkrBNBPoolHelper.depositNative - safeApprove without reset before depositFor into MasterMagpie

## Question
wombat/AnkrBNBPoolHelper.sol - _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Can an unprivileged attacker controlling msg.value and _minimumLiquidity, under the receipt token is minted to the helper while the credit is directed at a different address, exploit this through `depositNative(uint256 _minimumLiquidity)` to break the reconciliation between `IERC20(stakingToken).balanceOf(address(this)) delta` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` and the invariant that an approval on the deposit hot path must be idempotent, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the receipt token is minted to the helper while the credit is directed at a different address, have the attacker run `depositNative(uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `IERC20(stakingToken).balanceOf(address(this)) delta` versus `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` relation are unchanged by the attacker's transaction.
