# Q0856: ReferralStorage.trigger - a lone factor holder captures the entire BoostPoint

## Question
In rewards/ReferralStorage.sol, _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Does `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` let an unprivileged caller exploit that under the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, so that `BoostPoint` diverges from `totalBoostFactor`, the invariant that a shared boost budget must be diluted by absolute participation, not only by relative share is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: a lone factor holder captures the entire BoostPoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: a shared boost budget must be diluted by absolute participation, not only by relative share; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, have the attacker run `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, then assert the victim's claimable value and the `BoostPoint` versus `totalBoostFactor` relation are unchanged by the attacker's transaction.
