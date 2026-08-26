# Q0887: ReferralStorage.trigger - accrual is unfunded so claims are first-come first-served

## Question
In rewards/ReferralStorage.sol, trigger() only increments rewardAmount and no MGP is transferred into the contract at that moment, so the total of all rewardAmount values is a claim on whatever balance happens to be present and later claimers simply revert. Starting from a state where the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, can an unprivileged EOA use `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to leave `myReferer[account]` inconsistent with `userInfos[account].codeIUsed`, violating the invariant that every accrued entitlement must be backed by tokens already held and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: accrual is unfunded so claims are first-come first-served)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: trigger() only increments rewardAmount and no MGP is transferred into the contract at that moment, so the total of all rewardAmount values is a claim on whatever balance happens to be present and later claimers simply revert. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: every accrued entitlement must be backed by tokens already held; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, call `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, and assert `myReferer[account]` equals `userInfos[account].codeIUsed` and that no account can withdraw more than it put in.
