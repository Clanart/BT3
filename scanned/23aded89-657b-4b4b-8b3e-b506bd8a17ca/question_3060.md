# Q3060: ReferralStorage.trigger - sqrt factor makes many small accounts dominate the denominator

## Question
In rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Starting from a state where the attacker splits one large lock across many addresses that each register a code, can an unprivileged EOA use `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to leave `myReferer[account]` inconsistent with `userInfos[account].codeIUsed`, violating the invariant that a boost weight must not reward splitting one position across addresses and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker splits one large lock across many addresses that each register a code.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker splits one large lock across many addresses that each register a code, have the attacker run `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, then assert the victim's claimable value and the `myReferer[account]` versus `userInfos[account].codeIUsed` relation are unchanged by the attacker's transaction.
