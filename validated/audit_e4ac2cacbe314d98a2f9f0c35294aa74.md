### Title
Anyone can force a premature claim for any account via `multiclaimFor`, permanently forfeiting that account's unvested yield - ([File: rewards/MasterMagpie.sol])

### Summary
`MasterMagpie.multiclaimFor` is a public, unauthenticated function that lets any caller trigger a reward claim on behalf of an arbitrary `_account` [1](#0-0) . Just as the HackerOne report showed an attacker crafting a link that forces a victim's browser to send a state-changing `PUT` request the victim never intended, here an attacker forces an unrelated wallet's reward-harvest to execute at a time the victim never chose. Because the vlMGP/mWomSV reward path applies a time-based vesting decay (`getRewardablePercentWAD`) at the moment of harvest, forcing an early harvest permanently forfeits the portion of yield that has not yet vested for that victim.

### Finding Description
`multiclaimFor` takes `_stakingTokens`, `_rewardTokens`, and an arbitrary `_account` with no `msg.sender == _account` check and no allow-list, then calls `_multiClaim(_stakingTokens, _account, _account, _rewardTokens)` [1](#0-0) . `_multiClaim` routes through `_claimBaseRewarder`, which calls the pool's rewarder `getReward`/`getRewards` for `_account` [2](#0-1) .

In `vlMGPBaseRewarder`, both `getReward`/`getRewards` and the internal `_sendReward` call `_calExpireForfeit(_account, userRewards[...][_account])`, which computes `rewardablePercentWAD` from `vlMGP.getRewardablePercentWAD(_account)` and forfeits `_amount - rewardableAmount` [3](#0-2) . The forfeited amount is not returned to the victim; it is redistributed into the shared reward pool via `_queueNewRewardsWithoutTransfer`, benefiting other stakers instead of the account whose harvest was forced [4](#0-3) . `mWOMSVBaseRewarder` implements the identical `_calExpireForfeit`/`_sendReward` pattern [5](#0-4) .

Normally a rational user would only harvest once their `rewardablePercentWAD` reaches 100% (fully vested) to avoid forfeiture. `multiclaimFor` removes that choice: any third-party wallet can force the harvest at any moment, locking in whatever forfeiture percentage currently applies to the victim, and that forfeited yield is permanently transferred away from the victim into the common pool.

### Impact Explanation
This is a direct, permanent theft of a user's unclaimed/unvested yield: an attacker can call `multiclaimFor` for any staked account at a moment when that account's `rewardablePercentWAD` is low, causing a disproportionate share of that account's accrued rewards to be forfeited and redistributed to other participants (which can include the attacker if they are also staked in the same pool). The loss is irreversible once `_sendReward`/`queueMGP` executes since `userRewards[...][_account]` is zeroed and the forfeited amount is folded into `rewardPerTokenStored` for the whole pool.

### Likelihood Explanation
Trivial to execute: `multiclaimFor` is external and unauthenticated, requiring only knowledge of the victim's staking-token/pool list and the victim's address, both of which are public on-chain data. No special privileges, timing races, or governance access are required — any ordinary wallet can call it against any other ordinary wallet.

### Recommendation
Restrict `multiclaimFor` to a `require(msg.sender == _account)` model (i.e., merge it with `multiclaim`/`multiclaimSpec`) or require an explicit opt-in/approval mapping (similar to `multiclaimOnBehalf`'s `_onlyCompounder` restriction) before letting a third party trigger claims on behalf of another account, so that forfeiture-affecting harvests can only be initiated by the account owner or an approved compounder.

### Proof of Concept
1. Victim stakes into a Wombat pool and holds vlMGP with a `rewardablePercentWAD` currently at, e.g., 20% (still early in the vesting/decay curve as tracked by `VLMGP.getRewardablePercentWAD`).
2. Attacker (any wallet, unstaked or staked) calls `MasterMagpie.multiclaimFor(victimStakingTokens, rewardTokensArray, victimAddress)` [1](#0-0) .
3. This triggers `_multiClaim` → `_claimBaseRewarder` → `vlMGPBaseRewarder.getRewards`/`getReward` for the victim [2](#0-1) .
4. `_sendReward` computes `forfeitAmount = _calExpireForfeit(victim, userRewards[...][victim])` using the victim's current (low) `rewardablePercentWAD`, sends only the reduced `toSend` amount to the victim, and permanently redistributes `forfeitAmount` to the shared pool via `_queueNewRewardsWithoutTransfer` [3](#0-2) .
5. The victim's forfeited yield can never be reclaimed — it has been permanently transferred to other pool participants without the victim's consent, at a time and forfeiture rate the victim did not choose.

### Citations

**File:** rewards/MasterMagpie.sol (L412-417)
```text
    /// @notice Claims for each of the pools with specified rewards to claim for each pool
    function multiclaimFor(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, _account, _account, _rewardTokens);
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

**File:** rewards/vlMGPBaseRewarder.sol (L331-347)
```text
    function _queueNewRewardsWithoutTransfer(uint256 _amountReward, address _rewardToken) internal
    {
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards = rewardInfo.historicalRewards + _amountReward;
        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**vlMGPDecimal) / totalStaked();
        }
        emit ForfeitRewardAdded(_amountReward, _rewardToken);
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L363-400)
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

    function _earned(address _account, address _rewardToken, uint256 _userVlmgpShare) internal view returns (uint256) {
        return ((_userVlmgpShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**vlMGPDecimal) + userRewards[_rewardToken][_account];
    }

    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
        uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
        if (rewardableAmount > _amount)
            revert InvalidRewardableAmount();

        uint256 forfeitAmount = _amount - rewardableAmount;
        
        if (forfeitAmount < (_amount / 1000)) {  // if forfeitAmount is smaller than 0.1% ignore to save gas fee
            forfeitAmount = 0;
            rewardableAmount = _amount;
        }

        return forfeitAmount;
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L361-399)
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

    function _earned(address _account, address _rewardToken, uint256 _userMWOMSVShare) internal view returns (uint256) {
        return ((_userMWOMSVShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**mWOMSVDecimal) + userRewards[_rewardToken][_account];
    }

    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardableAmount = _amount;
        if (rewardableAmount > _amount)
            revert InvalidRewardableAmount();

        uint256 forfeitAmount = _amount - rewardableAmount;
        
        if (forfeitAmount < (_amount / 1000)) {  // if forfeitAmount is smaller than 0.1% ignore to save gas fee
            forfeitAmount = 0;
            rewardableAmount = _amount;
        }

        return forfeitAmount;
    }
}
```
