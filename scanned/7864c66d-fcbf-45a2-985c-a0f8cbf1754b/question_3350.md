# Q3350: ReferralStorage.trigger - trigger can be driven for any account through permissionless multiclaimFor

## Question
In rewards/ReferralStorage.sol, MasterMagpie.multiclaimFor lets any address force a settlement on any account, and _multiClaim ends by calling trigger(_user, totalReward), so referral accrual can be driven on schedule by a third party. Starting from a state where the referee has a large pending MGP claim in MasterMagpie, can an unprivileged EOA use `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to leave `codeOwners[_code]` inconsistent with `userInfos[account].myCode`, violating the invariant that referral accrual must follow the referee's own voluntary claim and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: trigger can be driven for any account through permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: MasterMagpie.multiclaimFor lets any address force a settlement on any account, and _multiClaim ends by calling trigger(_user, totalReward), so referral accrual can be driven on schedule by a third party. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: referral accrual must follow the referee's own voluntary claim; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the referee has a large pending MGP claim in MasterMagpie, snapshot `codeOwners[_code]` and `userInfos[account].myCode`, run the attacker's `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
