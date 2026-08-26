# Q1452: MasterMagpie.multiclaim - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
In rewards/MasterMagpie.sol, _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Starting from a state where the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, can an unprivileged EOA use `multiclaim(address[] _stakingTokens)` to leave `mgpPerSec` inconsistent with `IERC20(mgp).balanceOf(masterMagpie)`, violating the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `multiclaim(address[] _stakingTokens)`: constrain the setup so that the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, fuzz the attacker inputs (the full _stakingTokens array, including duplicates and unregistered addresses), and assert after every call that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users.
