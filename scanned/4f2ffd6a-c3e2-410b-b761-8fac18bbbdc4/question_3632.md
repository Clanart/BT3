# Q3632: ReferralStorage.trigger - accrual is unfunded so claims are first-come first-served

## Question
rewards/ReferralStorage.sol - trigger() only increments rewardAmount and no MGP is transferred into the contract at that moment, so the total of all rewardAmount values is a claim on whatever balance happens to be present and later claimers simply revert. Can an unprivileged attacker controlling the referee address and the block, because multiclaimFor is permissionless, under the attacker calls multiclaimFor on a set of referred accounts in one block, exploit this through `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to break the reconciliation between `codeOwners[_code]` and `userInfos[account].myCode` and the invariant that every accrued entitlement must be backed by tokens already held, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: accrual is unfunded so claims are first-come first-served)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: trigger() only increments rewardAmount and no MGP is transferred into the contract at that moment, so the total of all rewardAmount values is a claim on whatever balance happens to be present and later claimers simply revert. Precondition: the attacker calls multiclaimFor on a set of referred accounts in one block.
- Invariant to test: every accrued entitlement must be backed by tokens already held; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`: constrain the setup so that the attacker calls multiclaimFor on a set of referred accounts in one block, fuzz the attacker inputs (the referee address and the block, because multiclaimFor is permissionless), and assert after every call that every accrued entitlement must be backed by tokens already held.
