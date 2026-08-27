## Title
Reward claiming push-transfers can permanently freeze user yield if a reward token implements a blocklist - (File: rewards/BaseRewardPoolV2.sol)

### Summary
`BaseRewardPoolV2` (and the analogous `vlMGPBaseRewarder`/`mWOMSVBaseRewarder`) distribute arbitrary ERC20 tokens as staking rewards via `queueNewRewards`, and pay them out to users with a direct `safeTransfer` push inside `getReward`/`getRewards`. If a reward token implements an admin-controlled blocklist (e.g. USDC/USDT-style tokens), any transfer to a blocklisted staker will revert, permanently blocking that user from ever claiming their accrued rewards from that pool — exactly the same root cause described in the referenced report, but occurring in the protocol's core reward-claim path rather than the `revoke` function.

### Finding Description
`BaseRewardPoolV2.getReward`/`getRewards` iterate over all `rewardTokens` and push tokens straight to the account/receiver: [1](#0-0) [2](#0-1) 

The reward tokens themselves are arbitrary and added permissionlessly at the manager's discretion via `queueNewRewards`, with no restriction on token type: [3](#0-2) 

These `getReward`/`getRewards` calls are ultimately reachable by any ordinary staker through `MasterMagpie`'s unprivileged claim entry points such as `multiclaim`, `multiclaimSpec`, and even `multiclaimFor` (callable by anyone, on behalf of any `_account`, with rewards sent to that same `_account`): [4](#0-3) 

`_claimBaseRewarder` forwards the call into the rewarder's `getReward`/`getRewards`, and if any single reward token's transfer reverts (because the recipient is blocklisted by that token's issuer), the entire claim transaction reverts — including legitimate, non-blocklisted reward tokens bundled in the same call: [5](#0-4) 

The identical push-transfer-to-recipient pattern also exists in `vlMGPBaseRewarder._sendReward` and `mWOMSVBaseRewarder._sendReward`: [6](#0-5) [7](#0-6) 

If any of the reward tokens registered for a pool is a blocklist-capable token (USDC/USDT-style) and a staker's address later becomes blocklisted, that staker can no longer successfully call any claim function touching that pool — self-claim and third-party `multiclaimFor` calls alike will revert — because `userRewards[...] = 0` and the `safeTransfer` happen in the same transaction and both must succeed or the whole state change (including for other, unaffected reward tokens) is rolled back.

### Impact Explanation
This causes a permanent freezing of a user's already-earned, unclaimed yield: their accrued reward balance keeps accumulating in storage but can never be paid out as long as they are blocklisted for that reward token, since every code path that would clear/pay it reverts. It also blocks the same user from claiming any other, unaffected reward tokens bundled in the same pool/call due to the all-or-nothing loop in `getReward`. This matches the "theft or permanent freezing of unclaimed yield" impact category.

### Likelihood Explanation
Low — it requires a reward token added to a pool (via `queueNewRewards`) that implements an address blocklist feature (e.g. USDC/USDT), and requires the specific staker's address to be added to that blocklist by the token issuer. This mirrors the likelihood assessment of the referenced `revoke` finding.

### Recommendation
Adopt a pull-over-push pattern for reward payouts: instead of transferring reward tokens directly during `getReward`/`getRewards`, credit the amount to a withdrawable balance and let the user (or their designated receiver) separately call a `withdraw`-style function to pull the tokens. This isolates a blocklist-induced revert to only the affected token's withdrawal call rather than blocking claims for other reward tokens or corrupting the whole batched transaction.

### Proof of Concept
1. Manager calls `queueNewRewards` on a `BaseRewardPoolV2` pool with a blocklist-capable token (e.g., a USDC-like mock with an admin `blacklist(address)` function) as `_rewardToken`. [3](#0-2) 
2. A staker accrues rewards in that token through normal staking activity.
3. The token issuer blocklists the staker's address (external, off-protocol action — not a protocol admin action).
4. The staker calls `MasterMagpie.multiclaim`/`multiclaimSpec`, which routes to `BaseRewardPoolV2.getReward`, which loops through all reward tokens and calls `_sendReward`, attempting `IERC20(_rewardToken).safeTransfer(_receiver, _amount)` to the blocklisted address. [2](#0-1) 
5. The transfer reverts, reverting the entire claim transaction — permanently preventing the staker from claiming this reward token (and any other reward token bundled in the same call) for as long as they remain blocklisted.

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

**File:** rewards/BaseRewardPoolV2.sol (L273-286)
```text
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

**File:** rewards/BaseRewardPoolV2.sol (L323-327)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver, uint256 _amount) internal {
        userRewards[_rewardToken][_account] = 0;
        IERC20(_rewardToken).safeTransfer(_receiver, _amount);
        emit RewardPaid(_account, _receiver, _amount, _rewardToken);
    }
```

**File:** rewards/MasterMagpie.sol (L405-424)
```text
    /// @notice Claims for each of the pools with specified rewards to claim for each pool
    function multiclaimSpec(address[] calldata _stakingTokens, address[][] memory _rewardTokens)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, msg.sender, msg.sender, _rewardTokens);
    }

    /// @notice Claims for each of the pools with specified rewards to claim for each pool
    function multiclaimFor(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, _account, _account, _rewardTokens);
    }

    /// @notice Claims for each of the pools with specified rewards to claim for each pool. ONLY callable by compounder!!!!!!
    function multiclaimOnBehalf(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused _onlyCompounder
    {
        _multiClaim(_stakingTokens, _account, msg.sender, _rewardTokens);
    }
```

**File:** rewards/MasterMagpie.sol (L618-629)
```text
    /// @notice Harvest reward token in BaseRewarder for an account. NOTE: Baserewarder use user staking token balance as source to
    /// calculate reward token amount
    function _claimBaseRewarder(address _stakingToken, address _account, address _receiver, address[] memory _rewardTokens) internal {
        IBaseRewardPool rewarder = IBaseRewardPool(tokenToPoolInfo[_stakingToken].rewarder);
        if (address(rewarder) != address(0)) {
            if (_rewardTokens.length > 0)
                rewarder.getRewards(_account, _receiver, _rewardTokens);
            else
                // if not specifiying any reward token, just claim them all
                rewarder.getReward(_account, _receiver);
        }
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L363-377)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver) internal {
        uint256 forfeitAmount = _calExpireForfeit(_account, userRewards[_rewardToken][_account]);
        uint256 toSend = userRewards[_rewardToken][_account] - forfeitAmount;


        userRewards[_rewardToken][_account] = 0;
            
        if (toSend > 0) {
            IERC20(_rewardToken).safeTransfer(_receiver, toSend);
            emit RewardPaid(_account, _receiver, toSend, _rewardToken);
        }

        if(forfeitAmount > 0)
            _queueNewRewardsWithoutTransfer(forfeitAmount, _rewardToken);
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L362-376)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver) internal {
        uint256 forfeitAmount = _calExpireForfeit(_account, userRewards[_rewardToken][_account]);
        uint256 toSend = userRewards[_rewardToken][_account] - forfeitAmount;


        userRewards[_rewardToken][_account] = 0;
            
        if (toSend > 0) {
            IERC20(_rewardToken).safeTransfer(_receiver, toSend);
            emit RewardPaid(_account, _receiver, toSend, _rewardToken);
        }

        if(forfeitAmount > 0)
            _queueNewRewardsWithoutTransfer(forfeitAmount, _rewardToken);
    }
```
