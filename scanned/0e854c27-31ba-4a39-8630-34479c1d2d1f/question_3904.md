# Q3904: ReferralStorage.trigger - a lone factor holder captures the entire BoostPoint

## Question
In rewards/ReferralStorage.sol, _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Does `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` let an unprivileged caller exploit that under sharePercent is set so most of the split goes to the referee, so that `BoostPoint` diverges from `totalBoostFactor`, the invariant that a shared boost budget must be diluted by absolute participation, not only by relative share is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: a lone factor holder captures the entire BoostPoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Precondition: sharePercent is set so most of the split goes to the referee.
- Invariant to test: a shared boost budget must be diluted by absolute participation, not only by relative share; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`: constrain the setup so that sharePercent is set so most of the split goes to the referee, fuzz the attacker inputs (the referee address and the block, because multiclaimFor is permissionless), and assert after every call that a shared boost budget must be diluted by absolute participation, not only by relative share.
