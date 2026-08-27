### Title
Instant lock/unlock cycle into `mWOMSV` allows an attacker to dilute `rewardPerTokenStored` in `mWOMSVBaseRewarder` at the expense of long-term stakers - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`mWOMSVBaseRewarder._provisionReward` (invoked by `queueNewRewards`/`donateRewards`) computes `rewardInfo.rewardPerTokenStored += (_amountReward * 1e18) / totalStaked()`, where `totalStaked()` reads the live `IERC20(mWOMSV).totalSupply()` at the exact moment of the call. [1](#0-0) [2](#0-1)  Because this is a single global snapshot with no time-weighting/checkpointing, any address that increases `mWOMSV.totalSupply()` immediately before a reward donation and removes it immediately after permanently reduces the `rewardPerTokenStored` increment credited to that donation, diluting genuine long-term stakers' accrued rewards for that tranche.

### Finding Description
- `totalStaked()` returns `mWOMSV.totalSupply()` at call time, and is used as the sole denominator for converting an incoming reward amount into a global `rewardPerTokenStored` delta in `_provisionReward`. [3](#0-2) 
- The reward-per-share model is a standard "index += reward/totalSupply" pattern with **no minimum staking duration, no reward vesting period, and no per-block/per-second accrual** — the entire donated amount is priced against the instantaneous supply in the same transaction.
- A user's earned amount is `_earned = balance * (rewardPerToken - userRewardPerTokenPaid) / 1e18 + userRewards`, where `balance` comes from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account)`, not directly from `mWOMSV.balanceOf`. [4](#0-3) [5](#0-4)  Any address that can raise `mWOMSV.totalSupply()` transiently (via `lock`/`lockFor`, exposed through `SmartWomConvert.smartConvert`/`convert` with `_mode == 2`) dilutes `rewardPerTokenStored` for everyone else's tranche, then can withdraw before the next accrual — the contract does not gate this with a nonzero holding requirement. [6](#0-5) 
- `queueNewRewards`/`donateRewards` have no protection against being front-run by a `lock()` transaction, and `nonReentrant`/`onlyManager` only guard against re-entrancy and unauthorized callers — neither prevents supply-timing manipulation. [7](#0-6) 

**Critical gap in this analysis:** the actual `mWOMSV` locker contract that implements `lock()`, `lockFor()`, `startUnlock()`, `cancelUnlock()`, and `unlock()` — the component that would determine whether an attacker can truly mint/burn `mWOMSV` supply within a single transaction or block with no minimum holding period — is **not present in the indexed portion of this repository**. Only the `ILocker` interface was found; the concrete implementation (e.g., a `WomSV`/`MWomSV` contract) could not be located or read. This means I cannot confirm from repo code alone whether:
1. `lock()` mints `mWOMSV` (and thus increases `totalSupply()`) atomically within the same transaction as the attacker's flash-lock/unlock sequence, and
2. Whether `unlock()`/`cancelUnlock()` can genuinely return the attacker's principal in the same or very next transaction with no cooldown, penalty, or vesting that would neutralize the attack's profitability.

Given the reward math design is confirmed vulnerable in isolation (instantaneous-supply denominator, no time-weighting), but the actual exploitability hinges entirely on lock/unlock timing semantics that live in a contract this audit could not locate, this finding should be treated as **conditionally valid, pending verification of the concrete `mWOMSV` locker implementation**.

### Impact Explanation
If the underlying locker truly allows the described flash lock/unlock with no cooldown or penalty, an attacker can repeatedly siphon a proportional share of every reward donation from long-term stakers without bearing any lock-up risk, constituting theft of unclaimed yield from genuine long-term holders (Immunefi class: theft of unclaimed yield). The magnitude scales with the size of the attacker's capital relative to existing `totalStaked()` and is fully repeatable against every future `queueNewRewards`/`donateRewards` call.

### Likelihood Explanation
Preconditions: the attacker needs (a) knowledge of an imminent `queueNewRewards`/`donateRewards` transaction (visible in mempool since these are plain external calls with no access restriction on `donateRewards`, and `queueNewRewards` calls originate from `onlyManager` addresses such as `WombatStaking`, which are typically predictable/schedulable), and (b) capital to temporarily inflate `mWOMSV.totalSupply()`, which is returned immediately after unlocking (verification pending on the actual locker's unlock delay). This is feasible with commodity front-running/flashbots-style transaction ordering and requires no special privileges, consistent with the "unprivileged attacker" threat model.

### Recommendation
- Decouple reward distribution from an instantaneous `totalSupply()` snapshot: use a time-weighted or checkpointed staking model (e.g., accrue rewards per second against a supply that only counts balances held for at least one full accrual period), or require a minimum lock duration/cooldown before a `lock()`'d balance counts toward `totalStaked()`/`balanceOf()` for reward-earning purposes.
- Alternatively, restrict `donateRewards`/`queueNewRewards` timing predictability (e.g., streaming rewards over time rather than lump-sum donation) so an attacker cannot arbitrage a single-block supply spike.
- This recommendation is contingent on confirming, in the actual `mWOMSV` locker implementation, that `lock()`/`unlock()` indeed have no minimum holding period — that file should be located and reviewed to finalize remediation scope.

### Proof of Concept
Foundry test plan (pending access to the real `mWOMSV` locker contract to be conclusive):
1. Deploy `mWOMSVBaseRewarder`, `MasterMagpie`, and the real `mWOMSV` locker (not found in this repo scan — must be sourced from the deployment this audit targets).
2. Victim locks `X` mWom into `mWomSV` and registers stake via `MasterMagpie.depositFor(mWomSV, X, victim)` well in advance.
3. Attacker, in the transaction immediately preceding a pending `donateRewards`/`queueNewRewards` call (simulate via same-block or back-to-back txs), calls `mWomSV.lockFor(Y, attacker)` with `Y >> X`, inflating `mWOMSV.totalSupply()`.
4. Reward donation executes; `rewardPerTokenStored` increases by `(_amountReward * 1e18) / (X + Y)` instead of `(_amountReward * 1e18) / X`.
5. Attacker immediately calls `startUnlock`/`cancelUnlock`/`unlock` to exit position, then calls `getReward`/`getRewards` on `mWOMSVBaseRewarder` to claim their inflated proportional share `Y * rewardPerTokenStored / 1e18`.
6. Assert: victim's `earned(victim, rewardToken)` after the full cycle is strictly less than it would have been had the attacker never staked (`_amountReward * 1e18 / X` applied to `X`), and assert attacker's claimed amount is nonzero despite holding the position for a negligible number of blocks.

Because the concrete lock/unlock cooldown logic could not be located in this repository, this PoC cannot be fully executed against verified code; it should be completed against the actual `mWOMSV` locker source before treating this as a confirmed, exploitable finding.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L138-140)
```text
    function totalStaked() public override view returns (uint256) {
        return IERC20(address(mWOMSV)).totalSupply();
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L146-149)
```text
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L278-301)
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

    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }    
```

**File:** rewards/mWOMSVBaseRewarder.sol (L305-328)
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
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L378-383)
```text
    function _earned(address _account, address _rewardToken, uint256 _userMWOMSVShare) internal view returns (uint256) {
        return ((_userMWOMSVShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**mWOMSVDecimal) + userRewards[_rewardToken][_account];
    }
```

**File:** wombat/SmartWomConvert.sol (L209-214)
```text
        if (_mode == 1) {
            IERC20(mWom).safeApprove(masterMagpie, obtainedmWomAmount);
            IMasterMagpie(masterMagpie).depositFor(mWom, obtainedmWomAmount, _for);
        } else if (_mode == 2) {
            IERC20(mWom).safeApprove(address(mWomSV), obtainedmWomAmount);
            mWomSV.lockFor(obtainedmWomAmount, _for);
```
