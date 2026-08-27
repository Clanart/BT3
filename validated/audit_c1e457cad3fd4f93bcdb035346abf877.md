### Title
`donateRewards` lets an unprivileged attacker force-flush a queued reward backlog at a self-inflated `totalStaked()` denominator, permanently truncating unclaimed yield - (File: `rewards/BaseRewardPoolV2.sol`)

### Summary
`donateRewards(uint256 _amountReward, address _rewardToken)` is a permissionless wrapper around `_provisionReward` that anyone can call for any registered reward token, and its distribution math is gated only on the current, spot-readable value of `totalStaked()`, i.e. `IERC20(stakingToken).balanceOf(operator)`. An attacker can inflate that denominator in the same transaction (by depositing a large amount of the staking token into `MasterMagpie`), call `donateRewards` with as little as 1 wei to force the accumulated `rewards[_rewardToken].queuedRewards` backlog to be divided by the inflated `totalStaked()`, and then immediately withdraw the staked tokens - permanently and irrecoverably diluting the backlog's contribution to `rewardPerTokenStored`.

### Finding Description
`_provisionReward` decides how to book an incoming reward amount based on whether `totalStaked()` is zero at call time: [1](#0-0) 

If `totalStaked() == 0`, the amount is simply accumulated into `rewardInfo.queuedRewards` (this is the described precondition: a large backlog built up while nobody was staked). The moment `totalStaked()` becomes non-zero, the *next* call to `_provisionReward` (whether via `queueNewRewards` from the trusted `onlyManager` role, or via the completely permissionless `donateRewards`) folds the entire backlog plus the new amount and divides once by `totalStaked()`: [2](#0-1) 

`donateRewards` performs no access control beyond `isRewardToken[_rewardToken]` and accepts any `_amountReward`, including 1 wei:

Because `totalStaked()` reads `IERC20(stakingToken).balanceOf(operator)` directly (a live, manipulable value), and `operator` (`MasterMagpie`) exposes unrestricted `deposit`/`withdraw` for the staking token with no lock-up beyond `whenNotPaused`/`nonReentrant`, an attacker can, within a single transaction:
1. `MasterMagpie.deposit(stakingToken, hugeAmount)` — inflating `totalStaked()` to an arbitrarily large value.
2. `BaseRewardPoolV2.donateRewards(1, rewardToken)` — this flushes the entire pre-existing `queuedRewards` backlog by computing `rewardPerTokenStored += (backlog + 1) * 10**decimals / totalStaked()` using the attacker's inflated `totalStaked()`, then resets `queuedRewards` to 0.
3. `MasterMagpie.withdraw(stakingToken, hugeAmount)` — removing the attacker's stake, restoring `totalStaked()` to its prior (small) value. [3](#0-2) 

The backlog is consumed (`queuedRewards = 0`) and the corresponding `rewardPerTokenStored` increment is computed with integer division against an artificially huge denominator, so the real per-token credit rounds down to a negligible or zero value. No later call re-derives or restores the lost portion — `queuedRewards` was already zeroed, so the tokens sit in the contract's balance but are permanently unattributable to any staker's `rewardPerTokenStored` accounting.

No modifier, `onlyManager` check, or reward-index safeguard in `donateRewards`/`_provisionReward` prevents this, since the function intentionally allows any caller to provision rewards for UX/donation convenience, but does not protect the `totalStaked()` read from same-transaction manipulation.

### Impact Explanation
The queued backlog (which may represent a large amount of legitimately earned/queued reward tokens accumulated while the pool had zero stakers) is diluted by orders of magnitude via integer division against a temporarily and artificially inflated `totalStaked()`. Because `queuedRewards` is unconditionally zeroed once folded into `rewardPerTokenStored`, the truncated remainder is never recovered — the reward tokens remain physically locked in the contract but become permanently uncreditable to real stakers. This matches the Immunefi "High – Permanent freezing of unclaimed yield" impact class.

### Likelihood Explanation
The attack requires: (a) a reward token with an existing `queuedRewards` backlog (accumulated during a period with zero real stakers, which is a realistic bootstrap scenario for new pools), and (b) the attacker being able to deposit/withdraw a large amount of the pool's staking token in the same transaction, which is unrestricted (`MasterMagpie.deposit`/`withdraw` have no cooldown for ordinary staking tokens). The attacker needs capital (or a flash-loanable source of the staking token) proportional to how much dilution they want to cause, but the exploit itself costs only 1 wei of the reward token plus gas, and is fully repeatable against any registered reward token on any `BaseRewardPoolV2` pool that has accrued a queued backlog.

### Recommendation
Do not allow `donateRewards` (or any permissionless caller) to trigger the backlog-flushing branch of `_provisionReward` based on a spot-read, manipulable `totalStaked()`. Options: restrict `donateRewards` to only add to `queuedRewards` (never flush an existing backlog or compute `rewardPerTokenStored` directly), require `onlyManager` for any branch that divides by `totalStaked()`, or snapshot/time-average `totalStaked()` (e.g., checkpointed on stake/withdraw) instead of reading `balanceOf` live within the same transaction as an arbitrary caller's provisioning call.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `BaseRewardPoolV2` with a staking token and a registered reward token; set `totalStaked() == 0` (no deposits in `MasterMagpie`).
2. As the manager, call `queueNewRewards(largeBacklogAmount, rewardToken)` several times so `rewards[rewardToken].queuedRewards == largeBacklogAmount` while `totalStaked() == 0`.
3. As an unprivileged attacker EOA: mint/acquire `hugeStakeAmount` of the staking token, `approve` and `MasterMagpie.deposit(stakingToken, hugeStakeAmount)`.
4. Same attacker calls `BaseRewardPoolV2.donateRewards(1, rewardToken)` (after approving 1 wei of reward token).
5. Assert `rewards[rewardToken].queuedRewards == 0` and `rewards[rewardToken].rewardPerTokenStored` increased by only `(largeBacklogAmount+1) * 10**decimals / hugeStakeAmount`, which for a sufficiently large `hugeStakeAmount` truncates to a value orders of magnitude smaller than what real stakers would have received had the flush occurred at the pre-attack `totalStaked()`.
6. Attacker calls `MasterMagpie.withdraw(stakingToken, hugeStakeAmount)`, restoring `totalStaked()` to the small legitimate value.
7. Assert that summing `earned(realStaker, rewardToken)` over all real stakers is far less than `largeBacklogAmount`, and that the residual amount is unrecoverable (no future `queueNewRewards`/`donateRewards` call restores it since `queuedRewards` is already 0), demonstrating permanent freezing of the difference.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L255-260)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
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

**File:** rewards/MasterMagpie.sol (L334-346)
```text
    /// @notice Deposits staking token to the pool, updates pool and distributes rewards
    /// @param _stakingToken Staking token of the pool
    /// @param _amount Amount to deposit to the pool
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }

    /// @notice Withdraw staking tokens from Master Mgapie.
    /// @param _stakingToken Staking token of the pool
    /// @param _amount amount to withdraw
    function withdraw(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _withdraw(_stakingToken, msg.sender, _amount, false);
    }
```
