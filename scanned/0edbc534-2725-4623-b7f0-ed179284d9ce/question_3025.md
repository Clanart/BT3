# Q3025: WomUp.withdraw - stake and withdraw inside one block capture an interval

## Question
In wombat/WomUp.sol, stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Can an unprivileged attacker reach this through `withdraw(uint256 amount, bool claim)` while the attacker stakes one wei so _totalSupply is non-zero but every division truncates, and drive `lastUpdateTime` out of agreement with `periodFinish` - breaking the invariant that reward share must require the stake to have been held across the interval it is paid for - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: the attacker stakes one wei so _totalSupply is non-zero but every division truncates.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker stakes one wei so _totalSupply is non-zero but every division truncates, call `withdraw(uint256 amount, bool claim)`, and assert `lastUpdateTime` equals `periodFinish` and that no account can withdraw more than it put in.
