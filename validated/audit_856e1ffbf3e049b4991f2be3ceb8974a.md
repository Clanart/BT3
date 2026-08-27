## Analysis

The Moloch bug class is: a **push-style ERC20 transfer embedded in a critical state-changing function**, where a single blocked/reverting token transfer can permanently prevent that function from completing for everyone, freezing unrelated user funds. The equivalent pattern exists in `MasterMagpie`/`BaseRewardPool`. [1](#0-0) 
`getReward()` iterates over the **entire stored `rewardTokens` array** and unconditionally does `IERC20(rewardToken).safeTransfer(_receiver, reward)` for every token with a nonzero balance — with no try/catch and no per-token isolation. [2](#0-1) 
Both `_deposit` and `_withdraw`/`_harvestAndUnstake` in `MasterMagpie` unconditionally call `_harvestBaseRewarder`, which triggers `BaseRewardPool.getReward` for the pool's rewarder before the user's principal (staking/receipt token) is moved. [3](#0-2) 
Reward tokens are queued into a pool's rewarder via `queueNewRewards`, populated during bribe/reward harvesting from the external Wombat protocol — meaning the token list can include arbitrary ERC20s chosen by third-party bribers on Wombat, not just protocol-selected assets.

### Title
Unbounded push-transfer loop over all reward tokens in `BaseRewardPool.getReward` can permanently freeze user withdrawals/staking - (File: rewards/BaseRewardPool.sol, rewards/MasterMagpie.sol)

### Summary
`BaseRewardPool.getReward()` performs a push transfer for every registered reward token in a single loop with no isolation. Because `MasterMagpie._deposit`/`_withdraw` unconditionally call this harvest path before moving a user's staked principal, if any single reward token in the array becomes untransferable (blacklist-capable stablecoin freezing the pool contract, a token that later pauses, or a fee/hostile token registered via a Wombat bribe), the entire transaction reverts — blocking every staker of that pool from withdrawing or depositing, not just the affected account.

### Finding Description
`getReward` loops through `rewardTokens` and calls `safeTransfer` for each token with `reward > 0`, with no fallback if a transfer reverts: [4](#0-3) 

This function is reached from ordinary user actions. `MasterMagpie._withdraw` calls `_harvestAndUnstake`, which calls `_harvestBaseRewarder` for the pool before decrementing `user.amount`/`user.available` and transferring back the staking token: [5](#0-4) 

Similarly `_deposit` calls `_harvestBaseRewarder` before accepting new deposits: [6](#0-5) 

Reward tokens are appended to the array by `queueNewRewards`, which is called from `WombatStaking` while harvesting bribes/rewards from the external Wombat gauge/bribe system — a source of arbitrary, non-curated ERC20s: [7](#0-6) 

If one of these tokens later reverts on transfer to a specific pool contract (blacklist, pause, transfer-restriction, or is simply a malformed/malicious token registered by a briber), `getReward` — and therefore `withdraw`/`deposit`/`unstake` for **all** users of that staking pool — permanently reverts. There is no removal mechanism for a bad entry in `rewardTokens`, and no pull-based fallback, so the freeze is not self-healing without an unrelated code fix/migration. This mirrors the Moloch defect exactly: a single push-transfer failure embedded in a shared state-transition function blocks completion for other, unrelated participants, and funds become effectively "frozen" pending manual intervention.

### Impact Explanation
All stakers of the affected pool lose the ability to withdraw their staked principal (LP/receipt tokens held by `MasterMagpie`) and cannot deposit further, for as long as the poisoned reward token remains untransferable. Because the token is added by whichever address becomes untransferable/blacklisted for the pool address, and there is no removal path, this can constitute a freeze well beyond 24 hours — until a contract upgrade or migration is performed. This qualifies as permanent freezing of user funds (staked principal) and freezing of unclaimed yield in the same call, affecting every staker of the pool, not merely the party who caused the transfer failure.

### Likelihood Explanation
Reward tokens registered through Wombat bribe harvesting are not restricted to a vetted allow-list of tokens; any token used as a bribe by external, permissionless bribers on Wombat can end up queued as a `rewardToken`. Blacklist-capable stablecoins (USDT/USDC-style tokens) are common in DeFi and could plausibly blacklist a staking contract address, or a bribe token could simply be paused/rug-pulled. This does not require any admin/privileged action within this protocol — the trigger token is chosen by an external, unprivileged bribe provider.

### Recommendation
Wrap each `safeTransfer` in `BaseRewardPool.getReward`/`_updateFor`-style loops in a low-level call with try/catch (or use `call` and check success without reverting the whole loop), crediting failed transfers to a claimable-later balance (pull pattern) instead of reverting the entire harvest. Decouple reward harvesting from the deposit/withdraw principal-transfer path so a bad reward token cannot block movement of a user's own staked tokens.

### Proof of Concept
1. A briber on Wombat supplies a bribe in a token `X` for a gauge tied to a Magpie-integrated pool.
2. `WombatStaking` harvests the bribe and calls `IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, X)`, registering `X` in `rewardTokens` for that pool's `BaseRewardPool` — [8](#0-7) .
3. Token `X` is later blacklist-capable (or the pool contract's address gets blacklisted, or `X` becomes paused/reverting on transfer).
4. Any staker calling `withdraw` on the corresponding pool helper triggers `MasterMagpie.withdrawFor` → `_withdraw` → `_harvestAndUnstake` → `_harvestBaseRewarder` → `BaseRewardPool.getReward`, which reverts on `IERC20(X).safeTransfer(...)` — [4](#0-3)  and [9](#0-8) .
5. The entire withdrawal transaction reverts for every user of that pool who has any accrued balance of `X`, permanently locking their staked LP/receipt tokens until the contract is patched or the poisoned token is somehow made transferable again.

### Citations

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

**File:** rewards/MasterMagpie.sol (L482-505)
```text
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
```

**File:** rewards/MasterMagpie.sol (L507-534)
```text
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

**File:** wombat/WombatStaking.sol (L391-411)
```text
                        // if reward token is bnb, wrap it first
                        if (address(rewardTokens[i][j]) == address(0)) {
                            Address.sendValue(payable(wbnb), rewardAmount);
                            rewardTokens[i][j] = IERC20(wbnb);
                        }

                        uint256 protocolFee = (rewardAmount * bribeProtocolFee) / DENOMINATOR;

                        if (protocolFee > 0) {
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeFeeCollector, protocolFee);
                        }

                        if (caller != address(0) && bribeCallerFee != 0) {
                            callerFeeAmount = (rewardAmount * bribeCallerFee) / DENOMINATOR;
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeManager, callerFeeAmount);
                        }

                        rewardAmount -= protocolFee;
                        rewardAmount -= callerFeeAmount;
                        IERC20(rewardTokens[i][j]).safeApprove(_rewarders[i], rewardAmount);
                        IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, address(rewardTokens[i][j]));
```
