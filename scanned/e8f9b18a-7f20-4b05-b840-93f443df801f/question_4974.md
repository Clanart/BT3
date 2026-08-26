# Q4974: MasterMagpie.multiclaimSpec - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
Note that in rewards/MasterMagpie.sol, _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Can an attacker holding only tokens bought on market reach it via `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` under the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals and force `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` apart from `block.timestamp`, breaking the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, call `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`, and assert `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` equals `block.timestamp` and that no account can withdraw more than it put in.
