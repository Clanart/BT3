# Q3006: ReferralStorage.trigger - a lone factor holder captures the entire BoostPoint

## Question
In rewards/ReferralStorage.sol, _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Starting from a state where the attacker splits one large lock across many addresses that each register a code, can an unprivileged EOA use `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to leave `tiers[tierId].rewardPercentage + _calBoosted(referer)` inconsistent with `DENOMINATOR`, violating the invariant that a shared boost budget must be diluted by absolute participation, not only by relative share and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: a lone factor holder captures the entire BoostPoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Precondition: the attacker splits one large lock across many addresses that each register a code.
- Invariant to test: a shared boost budget must be diluted by absolute participation, not only by relative share; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence atomically under the attacker splits one large lock across many addresses that each register a code, asserting at the end that `tiers[tierId].rewardPercentage + _calBoosted(referer)` still equals `DENOMINATOR` and the PoC's balance delta is non-positive.
