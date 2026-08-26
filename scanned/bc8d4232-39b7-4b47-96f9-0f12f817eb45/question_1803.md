# Q1803: MasterMagpie.multiclaimFor - forced claim of a victim through permissionless multiclaimFor

## Question
Consider rewards/MasterMagpie.sol, where multiclaimFor(_stakingTokens, _rewardTokens, _account) has no access control and no msg.sender == _account check, so any address can force a settlement on any victim at a timestamp of the attacker's choosing. Assuming the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, can an unprivileged attacker turn this into a divergence between `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)` via `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, breaking the invariant that only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: forced claim of a victim through permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: multiclaimFor(_stakingTokens, _rewardTokens, _account) has no access control and no msg.sender == _account check, so any address can force a settlement on any victim at a timestamp of the attacker's choosing. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, call `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, and assert `mgpPerSec` equals `IERC20(mgp).balanceOf(masterMagpie)` and that no account can withdraw more than it put in.
