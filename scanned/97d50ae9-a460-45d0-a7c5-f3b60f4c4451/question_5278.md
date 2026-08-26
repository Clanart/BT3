# Q5278: WombatStaking.convertWOM - safeApprove without reset on the veWOM path

## Question
In wombat/WombatStaking.sol, convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Can an unprivileged attacker reach this through `convertWOM(uint256 _amount)` while the bonus reward token registered for the asset is also one of the fee currencies, and drive `feeInfos[i].value` out of agreement with `totalFee` - breaking the invariant that an approval on a hot path must be idempotent and must not be able to permanently disable conversion - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: safeApprove without reset on the veWOM path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: an approval on a hot path must be idempotent and must not be able to permanently disable conversion; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the bonus reward token registered for the asset is also one of the fee currencies, have the attacker run `convertWOM(uint256 _amount)`, then assert the victim's claimable value and the `feeInfos[i].value` versus `totalFee` relation are unchanged by the attacker's transaction.
