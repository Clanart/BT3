### Title
Reward claims can be permanently frozen if a single reward token blacklists the claimer, blocking multiclaim atomicity across pools - ([File: rewards/MasterMagpie.sol])

### Summary
`MasterMagpie._multiClaim` loops over multiple staking-token pools and, for each, calls `_claimBaseRewarder`, which in turn calls the pool's rewarder `getReward(_account, _receiver)` (or `getRewards`). Each rewarder (`BaseRewardPool.sol`, `BaseRewardPoolV2.sol`, `mWOMSVBaseRewarder.sol`, `vlMGPBaseRewarder.sol`) iterates over all registered reward tokens and does a `safeTransfer` to the fixed receiver in the same call/loop. If any single ERC20 reward token in that loop (e.g., a blacklist-capable stablecoin like USDC/USDT donated via `donateRewards`/`queueNewRewards`) reverts the transfer because the receiver is blacklisted, the entire `getReward`/`getRewards` call reverts, and since `_multiClaim` is a single atomic transaction spanning multiple staking-token pools, this reverts the claim of **all** other unrelated reward tokens across **all** pools included in that multiclaim call. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Finding Description
Reward recipients (`_account`/`_receiver`) are fixed to the caller (or the account being harvested for, via `multiclaimFor`/`multiclaimOnBehalf`) and cannot be redirected to a different address in the `getReward` call path — mirroring the root cause of the referenced report where redemption always targets `deposit.owner`/`msg.sender` with no way to change the destination. If that address is blacklisted by any single reward token contract, `IERC20(rewardToken).safeTransfer(_receiver, reward)` reverts, and this bubbles up through `getReward`/`getRewards` to `_claimBaseRewarder` and then to `_multiClaim`, reverting the whole batch. [5](#0-4) [6](#0-5) 

Some mitigation exists: `BaseRewardPoolV2.sol`, `mWOMSVBaseRewarder.sol`, and `vlMGPBaseRewarder.sol` expose `getRewards(_account, _receiver, _rewardTokens)` allowing selective claiming per reward token, so a user could, for a single pool, omit the blacklisted token from `_rewardTokens` to unblock the rest. [7](#0-6)  However, `BaseRewardPool.sol`'s `getRewards` implementation is an empty no-op stub, so pools using the plain `BaseRewardPool` cannot leverage this workaround at all — calling it with specific tokens silently does nothing. [8](#0-7)  More importantly, `multiclaimSpec`/`multiclaimFor` still call `_multiClaim` across the full array of `_stakingTokens` supplied, and a revert in any one pool's reward transfer aborts the transaction for **every** pool included in that call, since there's no per-pool try/catch isolation. [9](#0-8) 

Principal deposit/withdraw is not directly at risk: `_withdraw`/`_harvestAndUnstake` only call `rewarder.updateFor(_account)` (accrual bookkeeping only, no token transfer) before returning the staked token, so a blacklist cannot block withdrawal of the staked/underlying token itself. [10](#0-9) [11](#0-10) 

### Impact Explanation
A blacklisted user's accrued bonus/reward tokens (including any tokens accumulated in the affected `Reward` accounting, e.g. via `userRewards` mapping) become permanently unclaimable for that specific reward token, and — for pools relying on plain `BaseRewardPool` (whose `getRewards` selective-claim path is non-functional) — the user has no way to skip the poisoned reward token and thus cannot claim *any* rewards from that pool ever again, since every `getReward()` call for that pool will always attempt (and revert on) the blacklisted transfer. This is a permanent freeze of unclaimed yield for the affected user, matching the "permanent freezing of unclaimed yield" impact category.

### Likelihood Explanation
Requires (a) a reward token contract with blacklist capability (e.g., a stablecoin) to be added as a bonus reward via `queueNewRewards`/`donateRewards`, and (b) the claiming address to actually become blacklisted by that specific token's issuer — an external, low-frequency but realistic event for popular ERC20s. This is not an admin-triggered condition; any ordinary user whose wallet gets blacklisted by an unrelated third-party token issuer is affected purely by calling normal, unprivileged `multiclaim*` functions.

### Recommendation
Isolate reward-token transfer failures per token (e.g., wrap each `safeTransfer` in a low-level call with try/catch, or use a pull-based/escrow pattern crediting failed transfers to an internal withdrawable balance) so that one blacklisted/reverting reward token cannot block claims of other reward tokens or other pools within the same `multiclaim` call. Additionally, implement `BaseRewardPool.getRewards` properly (it is currently an empty no-op) so pools using the legacy rewarder can at least selectively skip a problematic token.

### Proof of Concept
1. Admin/manager calls `queueNewRewards`/`donateRewards` on a pool's `BaseRewardPool` to register a blacklist-capable ERC20 (e.g. USDC) as a bonus reward token: [12](#0-11) 
2. User accrues rewards in multiple pools/tokens by staking normally through `MasterMagpie.deposit`.
3. Token issuer blacklists the user's address (external event, outside the protocol).
4. User calls `multiclaimSpec`/`multiclaimFor` including the pool with the blacklisted reward token alongside other unrelated pools: [9](#0-8) 
5. `_multiClaim` iterates all requested pools and calls `_claimBaseRewarder` → `rewarder.getReward(_account, _receiver)`, which loops all reward tokens and calls `safeTransfer` to the blacklisted `_receiver`: [5](#0-4) 
6. The transfer reverts, reverting the entire multiclaim transaction, so the user cannot claim any of their rewards from any of the other pools included in the same call, and — for `BaseRewardPool`-based pools — has no working per-token opt-out since `getRewards` there is a no-op: [8](#0-7)

### Citations

**File:** rewards/MasterMagpie.sol (L405-417)
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
```

**File:** rewards/MasterMagpie.sol (L516-534)
```text
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

**File:** rewards/MasterMagpie.sol (L536-562)
```text
    function _multiClaim(address[] calldata _stakingTokens, address _user, address _receiver, address[][] memory _rewardTokens) internal nonReentrant {
        uint256 length = _stakingTokens.length;
        if (length != _rewardTokens.length) revert LengthMismatch();

        uint256 vlMGPPoolAmount;
        uint256 mWOmPoolAmount;
        uint256 defaultPoolAmount;

        for (uint256 i = 0; i < length; ++i) {
            address _stakingToken = _stakingTokens[i];
            UserInfo storage user = userInfo[_stakingToken][_user];
            
            updatePool(_stakingToken);
            uint256 claimableMgp = _calNewMGP(_stakingToken, _user) + unClaimedMgp[_stakingToken][_user];

            if (_stakingToken == address(vlmgp)) {
                vlMGPPoolAmount += claimableMgp;
            } else if (MPGRewardPool[_stakingToken]) {
                mWOmPoolAmount += claimableMgp;
            } else {
                defaultPoolAmount += claimableMgp;
            }

            unClaimedMgp[_stakingToken][_user] = 0;
            user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
            _claimBaseRewarder(_stakingToken, _user, _receiver, _rewardTokens[i]);
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

**File:** rewards/MasterMagpie.sol (L631-636)
```text
    /// only update the reward counting on in base rewarder but not sending them to user
    function _harvestBaseRewarder(address _stakingToken, address _account) internal {
        IBaseRewardPool rewarder = IBaseRewardPool(tokenToPoolInfo[_stakingToken].rewarder);
        if (address(rewarder) != address(0))
            rewarder.updateFor(_account);
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

**File:** rewards/BaseRewardPool.sol (L242-244)
```text
    function getRewards(address _account, address _receiver, address[] memory _rewardTokens) override external {

    }
```

**File:** rewards/BaseRewardPool.sol (L258-270)
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

**File:** rewards/BaseRewardPoolV2.sol (L237-250)
```text
    function getRewards(address _account, address _receiver, address[] memory _rewardTokens) override
        external
        onlyMasterMagpie
        updateRewards(_account, _rewardTokens)
    {
        uint256 length = _rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewardTokens[index];
            uint256 reward = userRewards[rewardToken][_account]; // updated during updateReward modifier
            if (reward > 0) {
                _sendReward(rewardToken, _account, _receiver, reward);
            }
        }
    }
```
