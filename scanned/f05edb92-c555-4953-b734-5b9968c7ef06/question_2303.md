# Q2303: ReferralStorage.trigger - trigger can be driven for any account through permissionless multiclaimFor

## Question
rewards/ReferralStorage.sol - MasterMagpie.multiclaimFor lets any address force a settlement on any account, and _multiClaim ends by calling trigger(_user, totalReward), so referral accrual can be driven on schedule by a third party. Can an unprivileged attacker controlling the referee address and the block, because multiclaimFor is permissionless, under the attacker locked vlMGP before registering a code, exploit this through `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to break the reconciliation between `userInfos[account].rewardAmount` and `MGP.balanceOf(address(this))` and the invariant that referral accrual must follow the referee's own voluntary claim, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: trigger can be driven for any account through permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: MasterMagpie.multiclaimFor lets any address force a settlement on any account, and _multiClaim ends by calling trigger(_user, totalReward), so referral accrual can be driven on schedule by a third party. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: referral accrual must follow the referee's own voluntary claim; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker locked vlMGP before registering a code, snapshot `userInfos[account].rewardAmount` and `MGP.balanceOf(address(this))`, run the attacker's `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
