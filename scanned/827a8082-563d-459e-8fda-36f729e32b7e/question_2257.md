# Q2257: ReferralStorage.trigger - a lone factor holder captures the entire BoostPoint

## Question
Note that in rewards/ReferralStorage.sol, _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Can an attacker holding only tokens bought on market reach it via `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` under the attacker locked vlMGP before registering a code and force `userInfos[account].rewardAmount` apart from `MGP.balanceOf(address(this))`, breaking the invariant that a shared boost budget must be diluted by absolute participation, not only by relative share for Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: a lone factor holder captures the entire BoostPoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: _calBoosted() returns BoostPoint * userInfos[_account].factor / totalBoostFactor, so whenever one account holds all or nearly all of totalBoostFactor its boost equals the full BoostPoint regardless of how small its absolute lock is. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: a shared boost budget must be diluted by absolute participation, not only by relative share; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`: constrain the setup so that the attacker locked vlMGP before registering a code, fuzz the attacker inputs (the referee address and the block, because multiclaimFor is permissionless), and assert after every call that a shared boost budget must be diluted by absolute participation, not only by relative share.
