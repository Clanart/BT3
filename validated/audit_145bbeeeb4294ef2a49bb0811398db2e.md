### Title
Single failing reward token permanently blocks claims of all other reward tokens in `BaseRewardPool`/`BaseRewardPoolV2` - (File: `rewards/BaseRewardPool.sol`, `rewards/BaseRewardPoolV2.sol`, `rewards/vlMGPBaseRewarder.sol`, `rewards/mWOMSVBaseRewarder.sol`)

### Summary
`getReward()` in the reward-pool family of contracts iterates over the full `rewardTokens[]` array and performs an unguarded `IERC20(rewardToken).safeTransfer` for every token with a non-zero pending balance. There is no isolation (no try/catch) between per-token transfers, and no admin function exists to remove a token from `rewardTokens[]` once added. If any single reward token in that array ever reverts on transfer to a given user, the whole `getReward` call reverts, permanently blocking that user from claiming every other (otherwise healthy) reward token in the same pool. This mirrors the referenced report's root cause: one failure point (there, `SponsorVault`; here, one entry of `rewardTokens[]`) is allowed to break an otherwise unrelated, broader flow — with no fail-safe such as a per-token try/catch, and unlike the `SponsorVault` case, no recovery path exists at all.

### Finding Description
`getReward(address _account, address _receiver)` loops over `rewardTokens` and unconditionally calls `safeTransfer` per token: [1](#0-0) [2](#0-1) 

The same unguarded-loop pattern exists in `BaseRewardPool.sol`: [3](#0-2) 

and in the vlMGP/mWOM staking-vote reward pools used for lock slots/forfeits: [4](#0-3) [5](#0-4) 

Tokens are appended to `rewardTokens[]` inside `queueNewRewards` and there is no corresponding function anywhere in these contracts to remove a token from that array once it is registered (confirmed by searching the codebase for a removal function — none exists): [6](#0-5) 

Because the array is iterated in full on every `getReward` call, and because a single reverting `safeTransfer` bubbles up and reverts the entire external call, one broken reward token permanently poisons claims of all the other, healthy reward tokens for every user of the pool. This differs from — and is strictly worse than — the referenced `SponsorVault` bug: in the Connext case the operator could call `setSponsorVault` to recover; here there is no equivalent way to purge or skip a bad token from `rewardTokens[]`, so the DoS is permanent rather than temporary.

### Impact Explanation
This results in permanent freezing of unclaimed yield: once any reward token entry in `rewardTokens[]` becomes non-transferable to any user who has an accrued non-zero balance of it (e.g., due to insufficient contract balance from reward-math rounding in `_provisionReward`, or the token later reverting on transfer for any reason), that user's `getReward` calls will revert forever, blocking withdrawal of every other legitimate, unrelated reward token balance they are entitled to in the same pool — with no recovery path since tokens can't be removed from `rewardTokens[]`. This satisfies the "permanent freezing of unclaimed yield" impact bar.

### Likelihood Explanation
Likelihood is moderate: it requires one reward token entry among potentially several per pool to become non-transferable for at least one staker (e.g., due to reward-accounting rounding shortfalls across `_provisionReward`/`rewardPerTokenStored` math causing the pool's tracked balance to exceed its actual token balance for that token). Given reward pools accumulate many tokens over their lifetime via `queueNewRewards`, and the reward math is not routed through any safety margin/reserve check, this is a plausible, unprivileged-triggerable failure mode, not one requiring a malicious actor.

### Recommendation
Isolate per-token transfers in `getReward`/`getRewards` with a try/catch (or a low-level call check) so failure to transfer one reward token does not prevent distribution of the others, and expose a way to deactivate/skip a permanently broken reward token in future iterations without reverting the whole claim.

### Proof of Concept
1. Manager calls `queueNewRewards` multiple times over time to register several reward tokens (e.g., WOM, MGP, USDC-like token) into `rewardTokens[]` for a given `BaseRewardPoolV2` instance [7](#0-6) .
2. Due to rounding accumulation in `_provisionReward`'s `rewardPerTokenStored` computation across many small `queueNewRewards` calls relative to `totalStaked()`, the pool's tracked `earned()` for one token can exceed the actual token balance held by the contract for that specific token [8](#0-7) .
3. Any staker calling `getReward` triggers the loop over `rewardTokens[]`; when it reaches the token with insufficient contract balance, `safeTransfer` reverts, reverting the entire `getReward` transaction [1](#0-0) .
4. Because there is no mechanism to remove that token from `rewardTokens[]`, every future `getReward` call for that account (and for any other account with a non-zero balance of that same token) reverts indefinitely, permanently freezing withdrawal of that user's legitimate balances of all other reward tokens in the pool.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L218-235)
```text
    function getReward(address _account, address _receiver)
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            uint256 reward = userRewards[rewardToken][_account]; // updated during updateReward modifier
            if (reward > 0) {
                _sendReward(rewardToken, _account, _receiver, reward);
            }
        }

        return true;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L270-286)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only callable by manager
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function queueNewRewards(uint256 _amountReward, address _rewardToken)
        override
        external
        onlyManager
        returns (bool)
    {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }

        _provisionReward(_amountReward, _rewardToken);
        return true;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L290-321)
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

        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingTokenDecimals) /
                totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }

    function _earned(address _account, address _rewardToken, uint256 _userShare) internal view returns (uint256) {
        return ((_userShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**stakingTokenDecimals) + userRewards[_rewardToken][_account];
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L323-327)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver, uint256 _amount) internal {
        userRewards[_rewardToken][_account] = 0;
        IERC20(_rewardToken).safeTransfer(_receiver, _amount);
        emit RewardPaid(_account, _receiver, _amount, _rewardToken);
    }
```

**File:** rewards/BaseRewardPool.sol (L219-240)
```text
    /// @notice Calculates and sends reward to user. Only callable by masterMagpie
    /// @param _account Address account
    function getReward(address _account, address _receiver)
        override
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            uint256 reward = userRewards[rewardToken][_account]; // updated during updateReward modifier
            if (reward > 0) {
                userRewards[rewardToken][_account] = 0;
                IERC20(rewardToken).safeTransfer(_receiver, reward);
                emit RewardPaid(_account, _receiver, reward, rewardToken);
            }
        }

        return true;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L232-247)
```text
    function getReward(address _account, address _receiver)
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }

        return true;
    }

```

**File:** rewards/mWOMSVBaseRewarder.sol (L233-247)
```text
    function getReward(address _account, address _receiver)
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }

        return true;
    }
```
