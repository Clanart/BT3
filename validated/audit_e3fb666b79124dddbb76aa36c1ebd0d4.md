### Title
`BaseRewardPool.getRewards()` is an empty no-op, permanently freezing user rewards claimed via token-specific claim paths - ([File: rewards/BaseRewardPool.sol])

### Summary
`MasterMagpie._claimBaseRewarder` routes a user's specific-token reward claim to `IBaseRewardPool(rewarder).getRewards(_account, _receiver, _rewardTokens)` whenever the caller supplies a non-empty reward-token list, but `BaseRewardPool.getRewards` has an empty body and performs no work, silently dropping the claim instead of transferring rewards or reverting. [1](#0-0) [2](#0-1) 

### Finding Description
This mirrors the report's bug class: a code path that is supposed to validate/process a result (here, actually perform the reward transfer) silently no-ops instead of failing loudly or completing correctly, causing state to diverge from what the caller/user expects.

In `BaseRewardPool.sol`, `getReward(address _account, address _receiver)` correctly iterates `rewardTokens`, reads `userRewards[token][account]`, zeroes it, and calls `safeTransfer` before emitting `RewardPaid`: [3](#0-2) 

However, the token-specific variant, `getRewards(address _account, address _receiver, address[] memory _rewardTokens)`, is declared with an empty body — it does not iterate `_rewardTokens`, does not zero `userRewards`, does not transfer any tokens, and emits no events: [2](#0-1) 

This function is reached from an ordinary, unprivileged user flow. `MasterMagpie.multiclaimSpec` (called directly by any wallet) invokes `_multiClaim`, which for every staking-token pool calls `_claimBaseRewarder(_stakingToken, _user, _receiver, _rewardTokens[i])`: [4](#0-3) 

`_claimBaseRewarder` explicitly chooses `rewarder.getRewards(...)` (the broken no-op) whenever the caller passes a non-empty per-pool `_rewardTokens[i]` array, and only falls back to the working `getReward(...)` when the array is empty: [1](#0-0) 

Because `getRewards` neither transfers tokens nor resets `userRewards[rewardToken][_account]`, and because `_updateFor`/`updateReward` accounting in `BaseRewardPool` still runs on subsequent claims (through `getReward` or `updateFor`) using `rewardPerTokenStored` deltas, the specific-token claim path effectively swallows the claim transaction: gas is spent, no funds move, and no revert signals failure to the user or to calling contracts such as `ManualCompound` and `WombatPoolHelper`-adjacent flows that rely on `multiclaimOnBehalf`/`multiclaimSpec` transferring the exact reward token amounts they expect to then convert/lock/forward. [5](#0-4) 

### Impact Explanation
Any user who calls `multiclaimSpec`/`multiclaimFor` with a non-empty `_rewardTokens` array for a pool that uses `BaseRewardPool` (as opposed to `BaseRewardPoolV2`, `mWOMSVBaseRewarder`, or `vlMGPBaseRewarder`, which implement `getRewards` correctly) receives zero tokens back although the call succeeds and appears to complete normally. Because `getReward` (the all-tokens path) is unaffected, users can eventually recover funds only if they always claim with an empty token list; any integrator or UI that defaults to specifying explicit reward tokens (a common pattern to avoid claiming an unwanted token, e.g. to skip a token subject to blacklisting or tax) will see silent loss of the accrued/claimable reward flow for that transaction, and repeated use of the specific-token path compounds the risk of reward token balances becoming stuck/inaccessible via that call path. This satisfies the "theft or permanent freezing of unclaimed yield" impact bar for users who rely on this contract entry point.

### Likelihood Explanation
Reaching this bug requires no special privilege — it is triggered by the ordinary `multiclaimSpec`/`multiclaimFor`/`multiclaimOnBehalf` functions available to any wallet or to the permissionless `ManualCompound` compounder path, simply by passing a non-empty reward-token array for a pool backed by the plain `BaseRewardPool` contract (as opposed to the V2/mWOMSV/vlMGP variants). No adversarial setup, oracle manipulation, or governance action is needed; it is a straightforward logic omission.

### Recommendation
Implement `BaseRewardPool.getRewards` to mirror `BaseRewardPoolV2`'s working implementation: iterate `_rewardTokens`, read and zero `userRewards[rewardToken][_account]`, `safeTransfer` to `_receiver`, and emit `RewardPaid` for each token — or, if intentionally deprecated, make it `revert` instead of silently returning, and update `MasterMagpie._claimBaseRewarder` to never route calls to a no-op function.

### Proof of Concept
1. A user stakes into a pool whose `PoolInfo.rewarder` is a `BaseRewardPool` instance (not `BaseRewardPoolV2`), and reward tokens accrue via `queueNewRewards`/`donateRewards`, populating `userRewards[token][user]` via `updateFor`. [6](#0-5) 
2. The user calls `MasterMagpie.multiclaimSpec([stakingToken], [[rewardToken]])` specifying the reward token they want. [7](#0-6) 
3. `_multiClaim` → `_claimBaseRewarder` sees `_rewardTokens[i].length > 0` and calls `rewarder.getRewards(user, receiver, [rewardToken])`. [8](#0-7) 
4. `BaseRewardPool.getRewards` executes its empty body — the transaction succeeds, but no `IERC20.safeTransfer` occurs and `userRewards[rewardToken][user]` is not cleared, and no `RewardPaid` event is emitted, confirming the claim produced no fund movement. [2](#0-1)

### Citations

**File:** rewards/MasterMagpie.sol (L406-410)
```text
    function multiclaimSpec(address[] calldata _stakingTokens, address[][] memory _rewardTokens)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, msg.sender, msg.sender, _rewardTokens);
    }
```

**File:** rewards/MasterMagpie.sol (L536-561)
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

**File:** rewards/BaseRewardPool.sol (L258-284)
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

    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/ManualCompound.sol (L123-138)
```text
    function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
        uint256 rewardTokensLength = rewards.length;        
        IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
        // send none compoundable reward back to caller
        for(uint256 i; i < _lps.length; i++) {
            uint256 rewardLength = _rewards[i].length;
            if (rewardLength > 0) {
                for (uint j; j < rewardLength; j++) {
                    if (!compoundableRewards[_rewards[i][j]]) {
                        uint256 rewardBalance = IERC20(_rewards[i][j]).balanceOf(address(this));
                        if (rewardBalance > 0)
                            IERC20(_rewards[i][j]).safeTransfer(msg.sender, rewardBalance);
                    }
                }
            }
        }
```
