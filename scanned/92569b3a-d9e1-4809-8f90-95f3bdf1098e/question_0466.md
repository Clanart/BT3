# Q0466: MasterMagpie.multiclaimSpec - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
Consider rewards/MasterMagpie.sol, where _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Assuming the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, can an unprivileged attacker turn this into a divergence between `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)` via `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`, breaking the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`: constrain the setup so that the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, fuzz the attacker inputs (both outer and inner arrays, so every reward-token address and its order), and assert after every call that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users.
