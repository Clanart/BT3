# Q2721: ReferralStorage.trigger - sqrt factor makes many small accounts dominate the denominator

## Question
In rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Can an unprivileged attacker reach this through `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` while the attacker cancels a cooldown so their real lock rises with no factor refresh, and drive `codeOwners[_code]` out of agreement with `userInfos[account].myCode` - breaking the invariant that a boost weight must not reward splitting one position across addresses - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker cancels a cooldown so their real lock rises with no factor refresh, call `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, and assert `codeOwners[_code]` equals `userInfos[account].myCode` and that no account can withdraw more than it put in.
