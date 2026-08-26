# Q0329: ReferralStorage.trigger - accrual is unfunded so claims are first-come first-served

## Question
In rewards/ReferralStorage.sol, trigger() only increments rewardAmount and no MGP is transferred into the contract at that moment, so the total of all rewardAmount values is a claim on whatever balance happens to be present and later claimers simply revert. Does `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` let an unprivileged caller exploit that under the attacker controls two addresses and binds one to the other's code, so that `codeOwners[_code]` diverges from `userInfos[account].myCode`, the invariant that every accrued entitlement must be backed by tokens already held is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: accrual is unfunded so claims are first-come first-served)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: trigger() only increments rewardAmount and no MGP is transferred into the contract at that moment, so the total of all rewardAmount values is a claim on whatever balance happens to be present and later claimers simply revert. Precondition: the attacker controls two addresses and binds one to the other's code.
- Invariant to test: every accrued entitlement must be backed by tokens already held; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`: constrain the setup so that the attacker controls two addresses and binds one to the other's code, fuzz the attacker inputs (the referee address and the block, because multiclaimFor is permissionless), and assert after every call that every accrued entitlement must be backed by tokens already held.
