# Q2681: ReferralStorage.trigger - accrual is unfunded so claims are first-come first-served

## Question
In rewards/ReferralStorage.sol, trigger() only increments rewardAmount and no MGP is transferred into the contract at that moment, so the total of all rewardAmount values is a claim on whatever balance happens to be present and later claimers simply revert. Does `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` let an unprivileged caller exploit that under the attacker cancels a cooldown so their real lock rises with no factor refresh, so that `userInfos[account].rewardAmount` diverges from `MGP.balanceOf(address(this))`, the invariant that every accrued entitlement must be backed by tokens already held is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: accrual is unfunded so claims are first-come first-served)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: trigger() only increments rewardAmount and no MGP is transferred into the contract at that moment, so the total of all rewardAmount values is a claim on whatever balance happens to be present and later claimers simply revert. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: every accrued entitlement must be backed by tokens already held; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker cancels a cooldown so their real lock rises with no factor refresh, snapshot `userInfos[account].rewardAmount` and `MGP.balanceOf(address(this))`, run the attacker's `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
