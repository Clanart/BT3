# Q2280: ReferralStorage.trigger - accrual is unfunded so claims are first-come first-served

## Question
rewards/ReferralStorage.sol - trigger() only increments rewardAmount and no MGP is transferred into the contract at that moment, so the total of all rewardAmount values is a claim on whatever balance happens to be present and later claimers simply revert. Can an unprivileged attacker controlling the referee address and the block, because multiclaimFor is permissionless, under the attacker locked vlMGP before registering a code, exploit this through `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to break the reconciliation between `userInfos[account].factor` and `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` and the invariant that every accrued entitlement must be backed by tokens already held, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: accrual is unfunded so claims are first-come first-served)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: trigger() only increments rewardAmount and no MGP is transferred into the contract at that moment, so the total of all rewardAmount values is a claim on whatever balance happens to be present and later claimers simply revert. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: every accrued entitlement must be backed by tokens already held; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence atomically under the attacker locked vlMGP before registering a code, asserting at the end that `userInfos[account].factor` still equals `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` and the PoC's balance delta is non-positive.
