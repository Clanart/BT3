# Q1340: WomUp.stake - stake and withdraw inside one block capture an interval

## Question
Consider wombat/WomUp.sol, where stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Assuming the reward period has just ended so periodFinish is behind block.timestamp, can an unprivileged attacker turn this into a divergence between `lastUpdateTime` and `periodFinish` via `stake(uint256 _amount)`, breaking the invariant that reward share must require the stake to have been held across the interval it is paid for and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: the reward period has just ended so periodFinish is behind block.timestamp.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the reward period has just ended so periodFinish is behind block.timestamp, have the attacker run `stake(uint256 _amount)`, then assert the victim's claimable value and the `lastUpdateTime` versus `periodFinish` relation are unchanged by the attacker's transaction.
