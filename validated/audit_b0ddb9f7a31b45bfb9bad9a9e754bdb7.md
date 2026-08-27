### Title
Instant same-block reward-skim via deposit→reward-inject→claim→withdraw drains pro-rata yield from long-term stakers - ([File: rewards/BaseRewardPoolV2.sol])

### Summary
`BaseRewardPoolV2` uses a lump-sum, non-time-weighted `rewardPerTokenStored` accounting model (Synthetix/Convex-style index, but without any streaming/duration mechanism). Any account holding a balance at the exact moment `queueNewRewards`/`donateRewards` is called captures its full pro-rata share of the newly injected reward, regardless of how long the stake was held, allowing an attacker to deposit immediately before a reward injection and withdraw immediately after to skim yield that should have accrued to long-term stakers. Note: the question's cited file `rewards/Airdrop2.sol` does not contain any staking/reward-index logic — that contract is a pure Merkle-proof vesting claim with no `balanceOf`/`rewardPerToken` mechanics — so the actual vulnerable code is in `rewards/BaseRewardPoolV2.sol`.

### Finding Description
`BaseRewardPoolV2._provisionReward` (called from `queueNewRewards` and the public `donateRewards`) immediately bumps the global index: [1](#0-0) 
using `totalStaked()` at the instant of injection, with no reward-rate/duration ("drip") model to time-weight distribution.

`_earned`/`_updateFor` then attribute reward strictly by current `balanceOf(_account)` relative to the delta in `rewardPerToken` since the account's last checkpoint: [2](#0-1) 

In `MasterMagpie._deposit`, the rewarder checkpoint (`_harvestBaseRewarder` → `rewarder.updateFor(_account)`) is called **before** `user.amount` is increased, so the attacker's `userRewardPerTokenPaid` is set to the pre-deposit index with zero attributed shares: [3](#0-2) 
Once the attacker's tokens are transferred in, `totalStaked()` (which reads `stakingToken.balanceOf(operator)`) already includes the attacker's stake for any subsequent `_provisionReward` call in the same block. When `getReward`/`getRewards` is then called, `updateReward`/`updateRewards` modifiers compute `_earned` using the attacker's full current `balanceOf`, crediting the attacker with a full pro-rata share of the newly injected reward even though the stake was held for effectively zero time: [4](#0-3) [5](#0-4) 

`_withdraw`/`_harvestAndUnstake` similarly re-checkpoints and lets the attacker exit immediately afterward: [6](#0-5) 

No mitigations exist: `donateRewards` is callable by any unprivileged address, is not `onlyManager`; `queueNewRewards` is `onlyManager` but manager-triggered injections are routine/expected events an attacker can front-run via mempool observation; there is no minimum staking duration, no reward streaming/duration window, and `nonReentrant` only blocks reentrancy, not sequential same-block transactions.

### Impact Explanation
This is a theft of unclaimed yield from other, genuinely long-term stakers — each reward injection's pro-rata allocation is a fixed pie; an attacker capturing a share proportional to `attackerBalance / totalStaked` at zero holding cost strictly reduces the share available to legitimate stakers who were staked before and after the injection. This matches the "theft of unclaimed yield" Immunefi impact class. The magnitude scales with the attacker's capital relative to pool TVL and the size of the reward injection they can front-run.

### Likelihood Explanation
- Preconditions: attacker needs staking-token liquidity (buyable/flash-loanable in many designs, though here `stakingToken` transfer requires actual balance, not necessarily flash-loanable depending on token) and pre-existing MGP/reward-token approvals are not required for this attack path (deposit/withdraw of the pool's staking token).
- The attack is mempool-front-runnable: attacker watches for pending `queueNewRewards`/`donateRewards` transactions and sandwiches them with `deposit` → (reward tx) → `getReward` → `withdraw`, all within one or two blocks.
- `donateRewards` requires no privilege at all, so an attacker could even self-trigger the reward injection using reward tokens they legitimately hold, immediately after depositing, purely to skim a share back proportional to their inflated presence — though the largest griefing value comes from front-running third-party/manager reward injections that fund the pool with rewards meant for existing long-term stakers.
- Fully repeatable for every future reward injection with no protocol-level countermeasure.

### Recommendation
Introduce a time-weighted reward distribution instead of an instant lump-sum index bump, e.g., adopt the standard `rewardRate`/`periodFinish`/`lastUpdateTime` streaming model (as used in Synthetix `StakingRewards` and Convex's newer reward pools) so that `rewardPerToken()` accrues continuously per second rather than jumping fully at the injection block. Alternatively, enforce a minimum staking duration before a deposit is eligible to earn from a given reward epoch, or snapshot eligible balances prior to reward injection so freshly deposited funds in the same block as (or immediately before) an injection are excluded from that specific reward distribution.

### Proof of Concept
Hardhat test plan:
1. Deploy `MasterMagpie`, register a pool with `BaseRewardPoolV2` rewarder for `stakingToken`/`rewardToken`.
2. Fund a long-term staker (Alice) who deposits `Y` staking tokens and waits several blocks with no other activity.
3. Fund attacker (Bob) with `X` staking tokens and reward tokens for `donateRewards` (or use a manager account to simulate a routine `queueNewRewards`).
4. In one block (using `hardhat_mine`/manual tx ordering or `evm_setAutomine(false)` + `evm_mine`), sequence: `Bob: deposit(X)` → `manager/Bob: queueNewRewards(R, rewardToken)` (or `donateRewards`) → `Bob: getReward via withdraw path` → `Bob: withdraw(X)`.
5. Assert: `rewardToken.balanceOf(Bob)` after withdrawal ≈ `R * X / (X + Y)` (full pro-rata share) despite Bob's holding period being effectively 0 blocks, while Alice's `earned(Alice, rewardToken)` is correspondingly reduced versus a scenario without Bob's front-run deposit.
6. Compare against expected "fair" allocation if reward were time-weighted (Bob should get ~0), demonstrating the conservation-invariant violation: total reward pool paid to Bob was not backed by any elapsed staking time contribution.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L102-120)
```text
    modifier updateReward(address _account) {
        _updateFor(_account);
        _;
    }

    modifier updateRewards(address _account, address[] memory _rewards) {
        uint256 length = _rewards.length;
        uint256 userShare = balanceOf(_account);
        
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewards[index];
            // if a reward stopped queuing, no need to recalculate to save gas fee
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;
            userRewards[rewardToken][_account] = _earned(_account, rewardToken, userShare);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
        _;
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

**File:** rewards/BaseRewardPoolV2.sol (L290-313)
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
```

**File:** rewards/BaseRewardPoolV2.sol (L316-321)
```text
    function _earned(address _account, address _rewardToken, uint256 _userShare) internal view returns (uint256) {
        return ((_userShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**stakingTokenDecimals) + userRewards[_rewardToken][_account];
    }
```

**File:** rewards/MasterMagpie.sol (L482-498)
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
```

**File:** rewards/MasterMagpie.sol (L508-534)
```text
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
