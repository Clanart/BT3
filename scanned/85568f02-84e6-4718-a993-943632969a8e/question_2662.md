# Q2662: WomUp.stake - stake and withdraw inside one block capture an interval

## Question
wombat/WomUp.sol - stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Can an unprivileged attacker controlling _amount and the block, with the WOM immediately converted 1:1 into mWOM, under the attacker calls getReward immediately after a large stake by another user, exploit this through `stake(uint256 _amount)` to break the reconciliation between `rewardPerTokenStored` and `userRewardPerTokenPaid[account]` and the invariant that reward share must require the stake to have been held across the interval it is paid for, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: the attacker calls getReward immediately after a large stake by another user.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls getReward immediately after a large stake by another user, call `stake(uint256 _amount)`, and assert `rewardPerTokenStored` equals `userRewardPerTokenPaid[account]` and that no account can withdraw more than it put in.
