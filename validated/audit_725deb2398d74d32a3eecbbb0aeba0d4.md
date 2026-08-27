### Title
Flash-loan sole-staker capture of `queuedRewards` backlog via permissionless `donateRewards` - (File: `rewards/BaseRewardPoolV2.sol`)

### Summary
`donateRewards` is callable by any address and simply forwards to `_provisionReward`, which routes new rewards into `queuedRewards` whenever `totalStaked() == 0` and otherwise flushes the *entire* `queuedRewards` balance plus the new amount into `rewardPerTokenStored` in a single step, scaled only by the `totalStaked()` observed at that instant. Because an attacker can both (a) become the sole staker via a flash-loaned deposit and (b) trigger the flush themselves with a 1-wei `donateRewards` call in the same transaction, they can redirect a backlog that was contributed by unrelated third parties entirely to themselves.

### Finding Description
`_provisionReward` (rewards/BaseRewardPoolV2.sol, lines 290-314) has two branches: [1](#0-0) 
- While `totalStaked() == 0` (no one has an active deposit in `MasterMagpie` for this staking token), every call — from `donateRewards` (open to anyone, `rewards/BaseRewardPoolV2.sol` lines 255-260) or `queueNewRewards` (manager-only) — only increments `rewardInfo.queuedRewards`, never touching `rewardPerTokenStored`.
- The moment `totalStaked() > 0`, the *entire* accumulated `queuedRewards` is merged with the new `_amountReward` and divided by the current `totalStaked()`, producing a single jump in `rewardPerTokenStored`.

`totalStaked()` reads `IERC20(stakingToken).balanceOf(operator)` (i.e., `MasterMagpie`'s balance for that token), so it becomes non-zero the instant any single account deposits via `MasterMagpie.deposit`/`depositFor`. [2](#0-1) 

Because reward accrual is purely `rewardPerTokenStored`-based and a user's earned amount is `userShare * (rewardPerToken - userRewardPerTokenPaid)`, whoever holds 100% of `totalStaked()` at the moment the flush happens captures 100% of the flushed backlog, regardless of how briefly they held that stake: [3](#0-2) 

Exploit sequence, all in one transaction:
1. Flash-loan the staking token.
2. Call `MasterMagpie.deposit(stakingToken, amount)` — this makes the attacker the sole staker (`totalStaked() == amount`). `_deposit` calls `_harvestBaseRewarder` → `rewarder.updateFor(account)` beforehand, but at this point `rewardPerTokenStored` is unchanged, so nothing is lost. [4](#0-3) 
3. Call `BaseRewardPoolV2.donateRewards(1, _rewardToken)` directly (unprivileged, only requires `isRewardToken[_rewardToken] == true`). Since `totalStaked() != 0` now, `_provisionReward` flushes the whole `queuedRewards` backlog (accrued from prior legitimate `queueNewRewards`/`donateRewards` calls while the pool sat empty) into `rewardPerTokenStored`, using the attacker's own stake as the sole denominator. [5](#0-4) 
4. Call `MasterMagpie.withdraw(stakingToken, amount)` to unwind the position and repay the flash loan; `_harvestAndUnstake` invokes `_harvestBaseRewarder`/`updateFor` again, crystallizing `userRewards[_rewardToken][attacker]` at the now-inflated `rewardPerTokenStored`. [6](#0-5) 
5. Separately call `multiclaim`/`getReward` to withdraw the accrued `_rewardToken` balance — no stake is required at claim time since `userRewards` is already recorded.

No modifier (`onlyManager`, `onlyMasterMagpie`, `nonReentrant`, `whenNotPaused`) blocks step 3, and `donateRewards` has no minimum amount or staker-eligibility check, so the entire backlog can be flushed with a 1-wei donation.

### Impact Explanation
This is a direct theft of unclaimed yield: real reward tokens transferred into the contract by legitimate reward providers while the pool held no stakers are diverted in full to a single flash-loan funded actor who never had at-risk capital and held a position for one block. Future genuine stakers who would otherwise have shared pro-rata in that backlog once staking resumed receive nothing. This matches "Critical - Direct theft of user funds / theft of unclaimed yield."

### Likelihood Explanation
Preconditions are attacker-controlled and cheap: (1) a period where `totalStaked() == 0` for the pool (plausible for newly created pools, or pools that fully empty during withdrawal migrations), (2) available flash-loan liquidity for the staking token, (3) knowledge of a registered reward token with `queuedRewards > 0`. No privileged role is required; the attacker only needs to call public `MasterMagpie.deposit`, public `donateRewards`, and public `MasterMagpie.withdraw`. It is fully repeatable each time the pool re-empties and refills.

### Recommendation
Do not allow a single-block staker to capture backlog accrued during a zero-stake period. Options:
- Track the timestamp/block at which `totalStaked()` first became non-zero after being zero, and require a minimum staking duration (or use a time-weighted accrual, distributing `queuedRewards` gradually rather than instantaneously) before backlog is folded into `rewardPerTokenStored`.
- Alternatively, gate `donateRewards` so it cannot trigger the flush transition within the same block/transaction as a deposit (e.g., disallow flush if `totalStaked()` changed in the current block), or restrict the flush from firing when the depositor triggering non-zero `totalStaked()` is also the caller of `donateRewards`.
- More generally, make `donateRewards` (or the flush logic) require that the pool has had non-trivial existing stake for some minimum window before absorbing `queuedRewards`, preventing an attacker from both creating and immediately harvesting the flush in one transaction.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `MasterMagpie`, a mock staking token, and `BaseRewardPoolV2` registered with a mock reward token; add the pool via `add(...)`.
2. As a "legitimate donor" account, call `queueNewRewards`/`donateRewards` multiple times while `totalStaked() == 0`; assert `rewards[token].queuedRewards` grows and `rewardPerTokenStored` stays 0.
3. Simulate a flash loan: mint/transfer staking tokens to the attacker contract, have it `approve` and call `MasterMagpie.deposit(stakingToken, amount)`.
4. From the same attacker transaction, call `BaseRewardPoolV2.donateRewards(1, rewardToken)`.
5. Assert `rewards[rewardToken].queuedRewards == 0` and `rewardPerTokenStored` jumped by `(oldQueued+1) * 1e_decimals / amount`.
6. Call `MasterMagpie.withdraw(stakingToken, amount)` to close the attacker's position and repay the flash loan (assert staking token balance is net-zero for attacker aside from any flash-loan fee).
7. Call `MasterMagpie.multiclaim`/`getReward` for the attacker and assert they receive (approximately) the full historical backlog of `rewardToken`, while a control "honest staker" who deposits after the attacker's withdrawal earns `0` of that backlog despite eventually holding 100% of `totalStaked()`.
8. Assert the invariant violation: the backlog reward tokens transferred into the contract by the donor were fully claimable by an account whose only "contribution" was a flash-loaned, same-block stake.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L126-128)
```text
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L255-260)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L301-312)
```text
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

**File:** rewards/MasterMagpie.sol (L482-497)
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
