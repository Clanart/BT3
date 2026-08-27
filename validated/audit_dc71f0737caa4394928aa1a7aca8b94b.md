### Title
`donateRewards` credits the full requested amount rather than the balance actually received, allowing fee-on-transfer/rebasing reward tokens to inflate `rewardPerTokenStored` beyond the pool's actual balance - (File: `rewards/BaseRewardPool.sol`)

### Summary
`_provisionReward()` in `rewards/BaseRewardPool.sol` transfers `_amountReward` via `safeTransferFrom` but then unconditionally credits `_amountReward` (the requested amount) to both `historicalRewards` and the `rewardPerTokenStored` increment, instead of measuring the actual balance delta received by the pool. Any registered reward token that delivers less than the requested amount on transfer (fee-on-transfer, rebasing-down, or any deflationary transfer behavior) causes the pool to promise more reward tokens than it actually holds, which is reachable by any unprivileged caller through the permissionless `donateRewards(uint256 _amountReward, address _rewardToken)` entrypoint.

### Finding Description
`donateRewards` has no access-control modifier and is callable by any address, as long as `_rewardToken` is already registered (`isRewardToken[_rewardToken] == true`): [1](#0-0) 

It forwards directly into `_provisionReward`, which performs the transfer and then computes the accrual purely from the caller-supplied `_amountReward`, never from the pool's before/after token balance: [2](#0-1) 

If `_rewardToken` is a fee-on-transfer or rebasing-down token, `IERC20(_rewardToken).safeTransferFrom(msg.sender, address(this), _amountReward)` moves less than `_amountReward` into the pool, yet `rewardInfo.historicalRewards` and `rewardInfo.rewardPerTokenStored` are incremented as if the full `_amountReward` had arrived. This breaks the invariant that "amount credited to the index == balance delta actually received." Over repeated calls, `rewardPerTokenStored` accrues faster than the pool's real token balance, so when legitimate stakers later call `getReward`, the last claimants will find the reward token balance insufficient to pay out what the index promises them — a form of protocol insolvency for that reward token.

Note: the part of the question suggesting `donateRewards` can desynchronize `rewardTokens.length` from `isRewardToken[_rewardToken]` does not hold — `donateRewards` never mutates `rewardTokens` or `isRewardToken`; only `queueNewRewards` does that push, and it does so consistently (push + flag set together). That specific invariant is not broken by this function. The `stakingDecimals()` external call on every accrual is real but is a gas/coupling concern, not itself a source of insolvency.

### Impact Explanation
Any registered reward token with fee-on-transfer or rebasing-down semantics lets the accounted `rewardPerTokenStored`/`historicalRewards` drift above the pool's real balance of that token. This is a genuine accounting/solvency bug: eventually stakers' `earned()` claims for that reward token will exceed what the pool can pay, causing some depositors to be unable to claim their full entitled rewards (funds effectively frozen/lost for the tail claimants). This matches "Protocol insolvency" for the affected reward token's accounting, though the scope is limited to whichever reward tokens the protocol has registered with transfer-fee/rebasing behavior — not applicable to standard ERC-20s used as reward tokens.

### Likelihood Explanation
Exploitability strictly depends on the protocol registering a fee-on-transfer or rebasing reward token via `queueNewRewards` (an owner/manager action) — this is a precondition outside attacker control. Given a qualifying reward token exists, any unprivileged address can trigger the drift repeatedly and for free capital efficiency (each call just needs enough of the reward token to cover `_amountReward`, down to 1 wei), making it fully repeatable once the precondition is met.

### Recommendation
In `_provisionReward`, measure the pool's actual balance before and after the `safeTransferFrom` call and use that delta (not the caller-supplied `_amountReward`) for `historicalRewards` and the `rewardPerTokenStored` computation, e.g.:
```solidity
uint256 balBefore = IERC20(_rewardToken).balanceOf(address(this));
IERC20(_rewardToken).safeTransferFrom(msg.sender, address(this), _amountReward);
uint256 received = IERC20(_rewardToken).balanceOf(address(this)) - balBefore;
// use `received` in place of `_amountReward` below
```

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `BaseRewardPool` with a mock fee-on-transfer ERC20 (e.g., burns 10% on every transfer) registered as a reward token via `queueNewRewards`.
2. Have a staker deposit into the staking token so `totalStaked() > 0`.
3. From an unprivileged EOA, call `donateRewards(1000e18, feeToken)` after approving the pool.
4. Assert: pool's actual `feeToken.balanceOf(pool)` increased by only 900e18 (fee deducted), but `rewards[feeToken].historicalRewards` and the `rewardPerTokenStored` increment reflect the full 1000e18.
5. Have all stakers call `getReward`; assert that the sum of `earned()` for `feeToken` across all stakers exceeds the pool's actual `feeToken` balance, demonstrating some stakers cannot be paid in full (insolvency).

### Citations

**File:** rewards/BaseRewardPool.sol (L279-284)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPool.sol (L297-319)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;
        if (this.totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingDecimals()) /
                this.totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
```
