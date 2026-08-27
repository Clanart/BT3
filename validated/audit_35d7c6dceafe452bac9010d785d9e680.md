### Title
`_provisionReward` credits the requested amount instead of the actual balance received, allowing fee-on-transfer/rebasing reward tokens to inflate `rewardPerTokenStored` beyond the pool's real balance - ([File: rewards/BaseRewardPool.sol])

### Summary
`donateRewards(uint256 _amountReward, address _rewardToken)` is a public, unprivileged entrypoint that forwards straight into `_provisionReward`, which credits `historicalRewards` and increments `rewardPerTokenStored` using the caller-supplied `_amountReward` rather than the actual token balance delta received by the pool. If any already-registered reward token has fee-on-transfer or rebasing semantics (delivering less than the nominal transfer amount), the pool's promised reward index exceeds the tokens it actually holds, so later legitimate `getReward` claims can revert or drain the pool's balance for other stakers, causing insolvency.

### Finding Description
`donateRewards` has no access control beyond requiring `isRewardToken[_rewardToken]` to be true: [1](#0-0) 

It calls `_provisionReward`, which does: [2](#0-1) 

The function calls `safeTransferFrom(msg.sender, address(this), _amountReward)` but never compares the pool's token balance before/after the transfer. It then unconditionally adds the full `_amountReward` to `historicalRewards` and uses it to compute the increment to `rewardPerTokenStored`:
```
rewardInfo.rewardPerTokenStored += (_amountReward * 10**stakingDecimals()) / totalStaked();
```
For a fee-on-transfer or rebasing reward token, the actual amount received by the pool can be strictly less than `_amountReward`. Since the index increment is based on the nominal amount, the pool's `rewardPerTokenStored` promises more tokens per staked unit than were actually deposited. Every staker's `earned()` value (computed from `rewardPerToken` in `earned()`) is inflated proportionally: [3](#0-2) 

Because `_updateFor` snapshots `userRewards[_rewardToken][account] = earned(_account, _rewardToken)` on every stake/withdraw/claim interaction, the inflated entitlement becomes permanently baked into `userRewards` for any account that interacts with the pool after the donation: [4](#0-3) 

There is no check anywhere in `BaseRewardPool.sol` that reconciles the credited amount against the pool's actual token balance, so the invariant "amount credited to the index equals balance delta actually received" is violated. Once enough over-promised reward is accumulated, whichever staker calls `getReward` last will find `IERC20(rewardToken).safeTransfer(_receiver, reward)` reverting (insufficient balance) or draining tokens intended for other stakers, i.e., some legitimate reward claims become permanently unfulfillable — protocol insolvency for that reward token.

`donateRewards` is callable by anyone, needs no special role, and only requires the target token to already be `isRewardToken[_rewardToken] == true` (added earlier via constructor or `queueNewRewards` by a manager). If the protocol has registered any deflationary/rebasing token as a reward token, an unprivileged attacker (or even an ordinary "good faith" donor) can trigger the accounting drift with as little as 1 wei, repeatable indefinitely.

### Impact Explanation
This breaks solvency of the reward accounting: `rewardPerTokenStored`/`userRewards` promise more reward tokens than the contract holds. This matches "Critical – Protocol insolvency," since it can cause permanent freezing/loss of legitimately earned rewards for some stakers when the pool runs out of the affected reward token balance during `getReward`.

### Likelihood Explanation
The exploit requires no privileged role — `donateRewards` is fully public and unguarded except for the `isRewardToken` check. The only real precondition is that a fee-on-transfer or rebasing token be already registered as a reward token in the specific pool (via manager-controlled `queueNewRewards`, which is outside attacker control but plausible for many real-world reward tokens). Given that precondition, the attack is trivially repeatable with arbitrarily small amounts (down to 1 wei) and requires no special capital or timing.

### Recommendation
In `_provisionReward`, measure the actual balance change instead of trusting `_amountReward`:
```solidity
uint256 balBefore = IERC20(_rewardToken).balanceOf(address(this));
IERC20(_rewardToken).safeTransferFrom(msg.sender, address(this), _amountReward);
uint256 received = IERC20(_rewardToken).balanceOf(address(this)) - balBefore;
// use `received` for historicalRewards / rewardPerTokenStored / queuedRewards accounting
```

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `BaseRewardPool` with a mock `stakingToken` (e.g., 0-2 decimal receipt token) and register a mock fee-on-transfer `rewardToken` (e.g., burns 10% on transfer) as a reward token via the constructor or a manager calling `queueNewRewards`.
2. Have two stakers deposit staking tokens through `MasterMagpie`/mocked `operator` so `totalStaked() > 0`.
3. As an unprivileged attacker (no manager role), call `donateRewards(_amountReward, rewardToken)` with `_amountReward = 1000`, while the pool only actually receives `900` due to the fee.
4. Assert: `rewards[rewardToken].historicalRewards` increased by `1000` (nominal) while `IERC20(rewardToken).balanceOf(pool)` only increased by `900` — violating the invariant that the credited amount equals the balance delta received.
5. Advance state so both stakers call `getReward`; show that the sum of `userRewards[rewardToken][staker1] + userRewards[rewardToken][staker2]` (as computed from the inflated `rewardPerTokenStored`) exceeds the pool's actual `rewardToken` balance, causing the second claimer's `getReward` call to revert due to insufficient token balance.

### Citations

**File:** rewards/BaseRewardPool.sol (L173-185)
```text
    function earned(address _account, address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return (
            (((balanceOf(_account) *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                (10**stakingDecimals())) + userRewards[_rewardToken][_account])
        );
    }
```

**File:** rewards/BaseRewardPool.sol (L279-284)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPool.sol (L288-295)
```text
    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            userRewards[rewardToken][_account] = earned(_account, rewardToken);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
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
