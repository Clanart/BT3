### Title
First-locker reward sniping via zero-totalStaked reward queuing in `mWOMSVBaseRewarder._provisionReward` - (File: `rewards/mWOMSVBaseRewarder.sol`)

### Summary
When `queueNewRewards`/`donateRewards` are called while `totalStaked()==0`, the reward amount is buffered in `rewardInfo.queuedRewards` instead of being distributed. The very next call to `_provisionReward` after any nonzero stake exists folds the entire queued amount into `rewardPerTokenStored`, dividing only by the current (possibly tiny) `totalStaked()`. Because `mWomSV.lock()` has no minimum amount and `donateRewards()` is a public, unrestricted, zero-cost trigger, any unprivileged attacker can become the sole staker with 1 wei and then self-trigger the fold to capture the entire previously queued reward pool.

### Finding Description
`_provisionReward` in `rewards/mWOMSVBaseRewarder.sol` implements: [1](#0-0) 

- If `totalStaked()==0` (i.e. `mWomSV` total supply is zero, meaning nobody currently has locked/cooling-down mWOM), any reward passed to `queueNewRewards` (manager-only) or `donateRewards` (public, callable by anyone for an already-registered token) accumulates in `rewardInfo.queuedRewards` rather than updating `rewardPerTokenStored`.
- The very next call to `_provisionReward` when `totalStaked() > 0` folds `queuedRewards` (plus any new `_amountReward`, which can be `0`) into `rewardPerTokenStored`, dividing by the *current* `totalStaked()`.

`mWomSV.lock()` performs no minimum-amount check: [2](#0-1) [3](#0-2) 

so an attacker can lock exactly `1 wei` of mWOM, becoming `totalStaked()==1`, the sole balance holder tracked via `balanceOf` in the rewarder: [4](#0-3) 

`donateRewards` is public and unrestricted (only requires the token be already registered), and does not enforce a nonzero `_amountReward`: [5](#0-4) 

So the attacker can call `donateRewards(0, rewardToken)` immediately after locking 1 wei — a zero-cost transaction — which enters the `else` branch of `_provisionReward` and folds the entire pre-existing `queuedRewards` into `rewardPerTokenStored / totalStaked()==1`, i.e. the reward-per-token becomes (almost) the entire queued reward amount. The attacker's `earned()` for that reward token then equals essentially the full queued reward, claimable via `getReward`/`getRewards` (gated only by `onlyMasterMagpie`, which any depositor reaches through normal flows): [6](#0-5) 

No existing modifier (`onlyManager`, `nonReentrant`, `whenNotPaused`) prevents this, because the exploit uses two legitimate, unprivileged calls (`lock` and `donateRewards`) in sequence, and the accounting bug is inherent to how `queuedRewards` is folded back using the instantaneous `totalStaked()` rather than being amortized or requiring a minimum stake/deposit floor.

### Impact Explanation
This results in theft of unclaimed yield: rewards that were queued while `totalStaked()==0` (intended to eventually be shared by the broader population of mWomSV lockers once they return) can be entirely captured by a single attacker holding a nominal 1-wei stake, at the expense of all future genuine lockers of `mWomSV` who receive no share of those rewards. This maps to Immunefi's "theft of unclaimed yield" impact class.

### Likelihood Explanation
The attack is fully unprivileged (uses only `lock` and `donateRewards`, both public/external, no special roles required) and cheap (1 wei principal, zero-value donation transaction). The limiting factor is the precondition that `totalStaked()==0` at reward-queuing time, which requires the `mWomSV` total supply to be exactly zero (e.g., pre-launch before genesis lockers, or a period where all lockers have fully unlocked). This is a narrow window, but plausible immediately after deployment/migration or during a full unlock event, and is realistically monitorable/front-runnable by an attacker watching for `queueNewRewards`/`RewardAdded` events while supply is zero.

### Recommendation
Prevent reward accrual from being divided by an artificially tiny `totalStaked()` right after a zero-supply period. Options: require a minimum bootstrap stake before `_provisionReward` folds queued rewards, track a virtual/minimum share denominator, or only release queued rewards pro-rata over time rather than crediting the entire backlog to whichever stake exists at the very next call. Also consider requiring `donateRewards`/`queueNewRewards` amounts to be greater than zero to prevent an attacker from self-triggering the fold without contributing real value.

### Proof of Concept
Foundry test outline:
1. Deploy `mWomSV`, `mWOMSVBaseRewarder`, and `MasterMagpie` mocks/wiring per existing test setup; ensure `totalStaked()==0` (no lockers).
2. As the reward manager, call `queueNewRewards(largeAmount, rewardToken)` on the rewarder while `totalStaked()==0` — assert `rewards[rewardToken].queuedRewards == largeAmount` and `rewardPerTokenStored` unchanged.
3. As attacker EOA, approve and call `mWomSV.lock(1)` to lock 1 wei, making `totalStaked()==1`.
4. As attacker, call `mWOMSVBaseRewarder.donateRewards(0, rewardToken)` — assert `rewardPerTokenStored` now reflects `largeAmount / 1` (scaled by 1e18).
5. Assert `rewarder.earned(attacker, rewardToken) == largeAmount` (or near it).
6. Have attacker call `getReward`/`getRewards` via MasterMagpie and assert they receive (approximately) the entire `largeAmount`, despite holding only 1 wei of the reward-bearing token and never having existed during the period the rewards were originally queued for.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L138-149)
```text
    function totalStaked() public override view returns (uint256) {
        return IERC20(address(mWOMSV)).totalSupply();
    }

    /// @notice Returns lock weighting of an user. Lock weighting is calculated by 
    /// amount of MGP still in lock + amount of MGP in cool down / 2
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L233-261)
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

**File:** rewards/mWOMSVBaseRewarder.sol (L296-301)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }    
```

**File:** rewards/mWOMSVBaseRewarder.sol (L305-327)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20Metadata(_rewardToken).safeTransferFrom(
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
                (_amountReward * 10**mWOMSVDecimal) / totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
```

**File:** wombat/mWomSV.sol (L226-230)
```text
    function lock(uint256 _amount) override external whenNotPaused nonReentrant {
        _lock(msg.sender, msg.sender, _amount);

        emit NewLock(msg.sender, block.timestamp, _amount);
    }
```

**File:** wombat/mWomSV.sol (L370-378)
```text
    function _lock(
        address spender,
        address _for,
        uint256 _amount
    ) internal {
        mWOM.safeTransferFrom(spender, address(this), _amount);
        IMasterMagpie(masterMagpie).depositMWomSVFor(_amount, _for);
        totalAmount += _amount; // trigers update pool share, so happens after toal amount increase
    }
```
