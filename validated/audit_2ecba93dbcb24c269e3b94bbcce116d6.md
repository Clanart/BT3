### Title
lpSupply/totalStaked inflation via direct token donation dilutes accMGPPerShare and rewarder rewardPerToken, permanently freezing a portion of emitted yield - (File: rewards/MasterMagpie.sol, rewards/BaseRewardPoolV2.sol)

### Summary
`_calLpSupply()` in `MasterMagpie.sol` and `totalStaked()` in `BaseRewardPoolV2.sol` both derive the reward-distribution denominator from `IERC20(_stakingToken).balanceOf(address(this))`/`balanceOf(operator)` rather than from a supply variable incremented only through `_deposit`/`_withdraw`. An attacker who owns 1 wei of the receipt/staking token (enough to make `lpSupply != 0`) can raw-transfer additional staking tokens directly to `MasterMagpie` without calling `deposit()`, inflating the denominator used in both `updatePool()` and `BaseRewardPoolV2._provisionReward()` without crediting any `UserInfo.amount` or increasing `balanceOf(account)` in the rewarder.

### Finding Description
- `_calLpSupply()` returns `IERC20(_stakingToken).balanceOf(address(this))` for any non-vlMGP/non-mWomSV pool [1](#0-0) .
- `updatePool()` divides the emitted `mgpReward` by this `lpSupply` to grow `pool.accMGPPerShare`: `pool.accMGPPerShare = pool.accMGPPerShare + ((mgpReward * 1e12) / lpSupply)` [2](#0-1) . This `mgpReward` amount is computed purely from `multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint`, independent of how much of `lpSupply` is actually credited to users via `UserInfo.amount`.
- Individual user claims are calculated strictly from `user.amount`, e.g. in `_calNewMGP`: `pending = (user.amount * accMGPPerShare) / 1e12 - user.rewardDebt` [3](#0-2) . If `lpSupply` used to grow `accMGPPerShare` is inflated by uncredited donated tokens, the sum of all `user.amount * Δ(accMGPPerShare)` across every staker is strictly less than the `mgpReward` that was "emitted" for that interval — the difference corresponds to a phantom share attributable to nobody, and is never claimable by anyone.
- The identical pattern exists in `BaseRewardPoolV2`: `totalStaked()` returns `IERC20(stakingToken).balanceOf(operator)` [4](#0-3) , while `balanceOf(_account)` reads the credited `userInfo[_stakingToken][_account].amount` via `IMasterMagpie(operator).stakingInfo` [5](#0-4) . `_provisionReward()` divides newly queued/donated reward tokens by `totalStaked()` to grow `rewardPerTokenStored`: `rewardInfo.rewardPerTokenStored += (_amountReward * 10**stakingTokenDecimals) / totalStaked()` [6](#0-5) . If `totalStaked()` is inflated by a raw donation directly to `MasterMagpie` (the `operator`), the reward-per-token growth is diluted relative to what legitimate stakers (whose `balanceOf` is unaffected) can claim, and the undistributed remainder — permanently orphaned since `sum(balanceOf(users)) < totalStaked()` — remains stuck in the rewarder contract with no code path to reclaim it.
- Legitimate deposits pass through `_deposit()`, which pulls tokens via `safeTransferFrom` and simultaneously increments `user.amount`/`user.available` [7](#0-6) , so under normal operation `balanceOf(this)`/`totalStaked()` naturally tracks the sum of credited stakes. The vulnerability is that nothing prevents an unprivileged actor from bypassing `_deposit()` entirely via a plain ERC20 `transfer()` call to the `MasterMagpie` contract address, which is indistinguishable from a legitimate stake in the balance-based accounting.
- `multiclaimFor(_stakingTokens, _rewardTokens, _account)` is a valid unprivileged path that both triggers `updatePool()` (crystallizing the diluted `accMGPPerShare`) and calls `_claimBaseRewarder` → `rewarder.getReward`/`getRewards` (crystallizing the diluted `rewardPerTokenStored` into `userRewards`) [8](#0-7) [9](#0-8) . No modifier (`onlyOwner`, `_onlyPoolHelper`, `_onlyCompounder`, etc.) restricts either the donation transfer or the call to `multiclaimFor`; only `whenNotPaused` and `nonReentrant` apply, neither of which prevents this.

### Impact Explanation
This causes permanent freezing of a portion of the MGP emission and of any secondary reward tokens queued through `queueNewRewards`/`donateRewards` on `BaseRewardPoolV2`. Once the denominator (`lpSupply`/`totalStaked()`) is inflated by uncredited donated balance, the fraction of `accMGPPerShare`/`rewardPerTokenStored` growth attributable to that phantom balance can never be claimed by any account, because no `UserInfo.amount` (and correspondingly no `balanceOf(account)` in the rewarder) exists to represent it. This matches the "High – Permanent freezing of unclaimed yield" impact class: real stakers permanently receive less MGP/reward tokens than were actually emitted/queued for the interval, and the shortfall is unrecoverable by any privileged or unprivileged action in the current code.

### Likelihood Explanation
The precondition (holding 1 wei of the pool's staking/receipt token so `lpSupply != 0`) is trivial and cheap to satisfy for essentially any pool token that is a standard transferable ERC20 (which receipt tokens registered in `tokenToPoolInfo` generally are, aside from the two hardcoded exceptions `vlmgp` and `mWomSV` which use `totalSupply()` instead of `balanceOf(this)`). The donation itself is a single unprivileged `transfer()` call requiring no special permission, no flash loan, and no reentrancy — capital cost is proportional only to the size of dilution the attacker/griefer wants to cause, and the attack is fully repeatable across any number of pools and reward intervals.

### Recommendation
Replace balance-based supply/stake tracking with an internally maintained accounting variable that is only mutated inside `_deposit`/`_withdraw` (and equivalent `depositFor`/`withdrawFor`/`depositVlMGPFor` paths), e.g., a `pool.totalStaked` field in `MasterMagpie.sol` incremented/decremented alongside `user.amount`, and expose that value from `BaseRewardPoolV2.totalStaked()` via `IMasterMagpie` instead of `IERC20(stakingToken).balanceOf(operator)`. This decouples reward-per-share accounting from raw ERC20 balances and makes it immune to direct-transfer donations.

### Proof of Concept
Hardhat test plan:
1. Deploy `MasterMagpie`, register a pool with a mock ERC20 `stakingToken` and a `BaseRewardPoolV2` rewarder with a mock reward token.
2. Attacker calls `deposit(stakingToken, 1)` to obtain `user.amount = 1`, making `lpSupply != 0`.
3. Advance time, then have the attacker `stakingToken.transfer(masterMagpie.address, 1_000_000e18)` directly (no `deposit()` call).
4. Have another (victim) account legitimately `deposit()` a normal amount, e.g., 100e18, before/after the donation.
5. Advance time by `T` seconds, then call `masterMagpie.multiclaimFor([stakingToken], [[]], victim)`.
6. Assert: `accMGPPerShare` growth for the interval equals `mgpReward*1e12/lpSupply` where `lpSupply` includes the donated 1_000_000e18, so the victim's claimed MGP is far less than `mgpPerSec*allocPoint*T/totalAllocPoint` (the actually emitted amount for that interval) scaled to victim's real share.
7. Separately, call `rewarder.queueNewRewards(rewardAmount, rewardToken)` after the donation, then have victim claim via `multiclaimFor`; assert `IBaseRewardPool(rewarder).balanceOf(victim)` (=100e18) times `rewardPerTokenStored` growth yields less than `rewardAmount`, with the shortfall permanently unclaimable since `sum(balanceOf(users)) (101e18) != totalStaked() (1,000,101e18)`.
8. Confirm no existing modifier, pause state, or reentrancy guard blocks steps 3-6, validating the unprivileged, reproducible nature of the freeze.

### Citations

**File:** rewards/MasterMagpie.sol (L379-388)
```text
        uint256 lpSupply = _calLpSupply(_stakingToken);
        if (lpSupply == 0) {
            pool.lastRewardTimestamp = block.timestamp;
            return;
        }        
        uint256 multiplier = block.timestamp - pool.lastRewardTimestamp;
        uint256 mgpReward = (multiplier * mgpPerSec * pool.allocPoint) / totalAllocPoint;
        
        pool.accMGPPerShare = pool.accMGPPerShare + ((mgpReward * 1e12) / lpSupply);
        pool.lastRewardTimestamp = block.timestamp;
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

**File:** rewards/MasterMagpie.sol (L609-616)
```text
    /// @notice calculate MGP reward based on current accMGPPerShare
    function _calNewMGP(address _stakingToken, address _account) view internal returns(uint256) {
        UserInfo storage user = userInfo[_stakingToken][_account];
        uint256 pending = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) /
            1e12 -
            user.rewardDebt;
        return pending;
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

**File:** rewards/MasterMagpie.sol (L659-667)
```text
    function _calLpSupply(address _stakingToken) internal view returns (uint256) {
        if (_stakingToken == address(vlmgp)) {
            return IERC20(address(vlmgp)).totalSupply();
        }
        if (_stakingToken == address(mWomSV)) {
            return IERC20(address(mWomSV)).totalSupply();
        }
        return IERC20(_stakingToken).balanceOf(address(this));
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L126-128)
```text
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L130-136)
```text
    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
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
