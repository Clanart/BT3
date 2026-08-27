### Title
Withdraw/deposit/claim on MasterMagpie reverts entirely if a single reward token in a pool's `rewardTokens` list becomes non-transferable, freezing user principal and other unclaimed rewards - (File: rewards/BaseRewardPool.sol / rewards/MasterMagpie.sol)

### Summary
`BaseRewardPool.getReward()` iterates over the full `rewardTokens` array of a pool and performs a plain `safeTransfer` for every token with a pending balance, with no try/catch. This function is invoked unconditionally as part of `MasterMagpie`'s deposit and withdraw flows (`_harvestBaseRewarder`), so if any single reward token in that array becomes stuck (paused token, blacklisting stablecoin like USDC, or any token whose transfer to the specific receiver reverts), the entire `deposit`, `withdraw`, and multiclaim transaction for that pool reverts, blocking users from withdrawing their principal or claiming any of the other, still-functional reward tokens.

### Finding Description
`BaseRewardPool.getReward` loops through all registered reward tokens and calls `IERC20(rewardToken).safeTransfer(_receiver, reward)` for each token with a nonzero pending amount, with no isolation between tokens: [1](#0-0) 

The same unprotected pattern exists in `BaseRewardPoolV2.getReward` / `_sendReward`: [2](#0-1) [3](#0-2) 

and in `vlMGPBaseRewarder.getReward` / `getRewards`: [4](#0-3) 

and `mWOMSVBaseRewarder.getRewards`: [5](#0-4) 

Reward tokens can be added to `rewardTokens` by any registered reward manager via `queueNewRewards`, and there is no mechanism to remove or skip a reward token once it stops functioning (e.g., protocol pauses transfers, an account got blacklisted by a censorable stablecoin, or the reward token contract itself is bricked). Crucially, `MasterMagpie._deposit` and `MasterMagpie._harvestAndUnstake` (used by both `_deposit` and `_withdraw`) unconditionally call `_harvestBaseRewarder`, which in turn calls `getReward` on the pool's base rewarder, before allowing the user's stake/unstake accounting to proceed: [6](#0-5) [7](#0-6) 

Because `getReward` reverts as soon as one reward token's `safeTransfer` reverts, and the harvest call is not wrapped in try/catch anywhere in `MasterMagpie`, a single broken reward token in a pool's reward list poisons the entire pool: users can no longer `deposit`, `withdraw`, or claim rewards for that staking token, even though their staked principal and other, healthy reward tokens are otherwise fine. This mirrors the reported Union Finance issue where one broken/paused adapter blocks unrelated deposit/withdraw/rebalance calls, except here the "adapters" are the individual reward tokens accumulated inside a `BaseRewardPool`.

### Impact Explanation
Once a reward token in a pool becomes non-transferable (e.g., a blacklisting stablecoin or a token contract that gets paused/bricked, which is realistic and outside the protocol's control since token additions are permissionless from the manager side and cannot later be un-registered), every unprivileged user staked in that pool is permanently unable to withdraw their principal via the normal `withdraw`/`withdrawFor` path, and permanently unable to claim any of the other legitimately earned reward tokens for that pool. This is a direct, indefinite freezing of user principal and unclaimed yield with no recovery path in the reachable contract code, satisfying the "permanent freezing of funds" / "24-hour-plus freeze" bar.

### Likelihood Explanation
Any reward token added to a pool (stablecoins with blacklist functionality are common bonus/incentive tokens) can independently become non-transferable at any point after being queued, and this requires no malicious or privileged action by governance/admins of this protocol — it is triggered by ordinary external token behavior (e.g., a user or the contract itself getting blacklisted, or the token pausing). Given that reward tokens are routinely added via `queueNewRewards` and never removed, the likelihood of eventually hitting this condition over the life of a pool is significant.

### Recommendation
Wrap each per-token `safeTransfer` in `getReward`/`_sendReward` (across `BaseRewardPool`, `BaseRewardPoolV2`, `vlMGPBaseRewarder`, `mWOMSVBaseRewarder`) in a try/catch (or use a low-level `call` and check success, retaining the reward balance for later retry) so that a failure on one reward token does not block harvesting/transferring of the other reward tokens or the underlying stake/unstake operation in `MasterMagpie`.

### Proof of Concept
1. A reward manager calls `BaseRewardPool.queueNewRewards` to register `TokenX` (e.g., a censorable stablecoin) as a bonus reward token for pool `P`, per [8](#0-7) .
2. `TokenX` later blacklists the `BaseRewardPool` contract address or the receiving user address (or the token is paused/bricked).
3. A user staked in pool `P` calls `MasterMagpie.withdrawFor`/`withdraw`, which triggers `_withdraw` → `_harvestAndUnstake` → `_harvestBaseRewarder` → `BaseRewardPool.getReward`, per [9](#0-8) .
4. Inside `getReward`'s loop, the `safeTransfer` call for `TokenX` reverts, per [10](#0-9) , causing the entire `withdraw` transaction to revert.
5. The user's principal (and all other reward tokens in pool `P`) become permanently locked/unclaimable as long as `TokenX` remains non-transferable, with no way to bypass the harvest step in the current code.

### Citations

**File:** rewards/BaseRewardPool.sol (L221-240)
```text
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

**File:** rewards/BaseRewardPool.sol (L261-274)
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

**File:** rewards/BaseRewardPoolV2.sol (L323-327)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver, uint256 _amount) internal {
        userRewards[_rewardToken][_account] = 0;
        IERC20(_rewardToken).safeTransfer(_receiver, _amount);
        emit RewardPaid(_account, _receiver, _amount, _rewardToken);
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L232-260)
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

    function getRewards(address _account, address _receiver, address[] memory _rewardTokens)
        public
        onlyMasterMagpie
        updateRewards(_account, _rewardTokens)
        nonReentrant
    {
        uint256 length = _rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L241-261)
```text
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }

        return true;
    }

    function getRewards(address _account, address _receiver, address[] memory _rewardTokens)
        public
        onlyMasterMagpie
        updateRewards(_account, _rewardTokens)
        nonReentrant
    {
        uint256 length = _rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }
    }
```

**File:** rewards/MasterMagpie.sol (L481-534)
```text
    /// @notice internal function to deal with deposit staking token
    function _deposit(address _stakingToken, address _account, uint256 _amount, bool _isVlmgp) internal {
        updatePool(_stakingToken);

        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][_account];

        if (user.amount > 0) {
            _harvestMGP(_stakingToken, _account);
        }
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount + _amount;
        if (!_isVlmgp) {
            user.available = user.available + _amount;
            IERC20(pool.stakingToken).safeTransferFrom(address(msg.sender), address(this), _amount);
        }
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;

        if (_amount > 0)
            if (!_isVlmgp)
                emit Deposit(_account, _stakingToken, _amount);
            else
                emit DepositNotAvailable(_account, _stakingToken, _amount);
    }

    /// @notice internal function to deal with withdraw staking token
    function _withdraw(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        _harvestAndUnstake(_stakingToken, _account, _amount, _isVlMgp);

        if (!_isVlMgp)
            IERC20(tokenToPoolInfo[_stakingToken].stakingToken).safeTransfer(address(msg.sender), _amount);
        emit Withdraw(_account, _stakingToken, _amount);
    }

    function _harvestAndUnstake(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        updatePool(_stakingToken);

        UserInfo storage user = userInfo[_stakingToken][_account];

        if (!_isVlMgp && user.available < _amount)
            revert WithdrawAmountExceedsStaked();
        else if(user.amount < _amount && _isVlMgp)
            revert UnlockAmountExceedsLocked();
        
        _harvestMGP(_stakingToken, _account);
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount - _amount;
        
        if(!_isVlMgp)
            user.available = user.available - _amount;
        user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
    }
```
