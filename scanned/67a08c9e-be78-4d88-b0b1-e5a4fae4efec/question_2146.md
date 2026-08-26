# Q2146: WomUp.withdraw - stake and withdraw inside one block capture an interval

## Question
wombat/WomUp.sol: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. With amount and whether the claim leg runs in the same call under attacker control and the attacker migrates and withdraws inside one transaction, can an unprivileged caller sequence `withdraw(uint256 amount, bool claim)` so that `_totalSupply` and `IERC20(mWom).balanceOf(address(this))` no longer reconcile, violating the invariant that reward share must require the stake to have been held across the interval it is paid for and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: the attacker migrates and withdraws inside one transaction.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker migrates and withdraws inside one transaction, call `withdraw(uint256 amount, bool claim)`, and assert `_totalSupply` equals `IERC20(mWom).balanceOf(address(this))` and that no account can withdraw more than it put in.
