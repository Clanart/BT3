### Title
Direct ERC20 donation to MasterMagpie inflates `_calLpSupply()`, permanently diluting `accMGPPerShare` and freezing unclaimed MGP yield - (File: rewards/MasterMagpie.sol)

### Summary
`_calLpSupply()` computes the reward denominator for any non-vlMGP/non-mWomSV pool as the raw `balanceOf(address(this))` of the staking token, rather than a tracked sum of credited `UserInfo.amount`. Any unprivileged holder of that pool's staking token can `transfer()` tokens directly to `MasterMagpie` (bypassing `deposit()`), permanently inflating the divisor used in `updatePool()`'s `accMGPPerShare` calculation without ever crediting a `UserInfo.amount` to reclaim that share, causing a portion of every future MGP emission to become permanently unclaimable by any user.

### Finding Description
`updatePool()` computes the per-share reward increment as: [1](#0-0) 
where `lpSupply` comes from `_calLpSupply()`: [2](#0-1) 

For `vlmgp` and `mWomSV` pools the developers correctly use `totalSupply()` of the locker token (which can only change through controlled mint/burn), but for every other registered pool the divisor is simply `IERC20(_stakingToken).balanceOf(address(this))`. Under intended operation this balance is expected to equal the sum of all `UserInfo.amount` for that token, since deposits/withdrawals move tokens exclusively via `_deposit`/`_withdraw` using `safeTransferFrom`/`safeTransfer`: [3](#0-2) [4](#0-3) 

However, nothing prevents an unprivileged holder of the staking token from calling `IERC20(stakingToken).transfer(masterMagpie, amount)` directly, which increases `balanceOf(address(this))` without touching any `UserInfo.amount`. On the next `updatePool()`/`multiclaim()` call, `lpSupply` is inflated by the donated amount, so `mgpReward * 1e12 / lpSupply` is permanently smaller than it should be relative to the real staked total. Since `_multiClaim()` (invoked by `multiclaim()`) resets `rewardDebt` to `user.amount * accMGPPerShare / 1e12` for every claimant: [5](#0-4) 
the gap between the "fair" `accMGPPerShare` (based on real staked supply) and the actual diluted `accMGPPerShare` is never recovered — the donated balance holds no `UserInfo` entry that could ever claim the missing share, and the attacker's own donated tokens are also unrecoverable (no code path exists to withdraw balance beyond `user.available`, which only tracks real deposits). The result is that a fraction of the MGP tokens the contract is meant to distribute for that pool's interval becomes permanently stuck/unclaimable.

### Impact Explanation
Every second that the donated balance remains in the contract, the pool's `accMGPPerShare` accrues at a permanently reduced rate proportional to `real_supply / (real_supply + donated_amount)`. Because reward tokens are transferred out of the contract's own MGP balance only via `_calNewMGP`/`accMGPPerShare` bookkeeping, the undistributed remainder is never assigned to any `UserInfo` and can never be claimed by anyone — matching the "permanent freezing of unclaimed yield" impact class. This harms all current and future stakers of that pool, not just the attacker.

### Likelihood Explanation
The attack is trivially reachable by any unprivileged holder of the affected staking token (no special role required) and needs only a single `transfer()` call plus normal contract flow (`updatePool`/`multiclaim`). However, it is a costly griefing action: the attacker permanently loses the tokens donated (since they gain no corresponding `UserInfo.amount` and cannot withdraw them), so there is no direct profit motive for the attacker — the primary effect is economic damage to other stakers via reward dilution, which is repeatable and cumulative for as long as the donated balance sits in the contract.

### Recommendation
Track staked supply explicitly (e.g., a `poolTotalStaked[_stakingToken]` counter incremented/decremented only in `_deposit`/`_withdraw`) instead of relying on `IERC20(_stakingToken).balanceOf(address(this))` in `_calLpSupply()`. This mirrors the fix already applied for `vlmgp`/`mWomSV` (using `totalSupply()` rather than balance) and eliminates sensitivity to unsolicited direct token transfers.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `MasterMagpie`, an MGP token, and a mock ERC20 staking token; `add()` the staking token as the sole pool with `allocPoint > 0` so `totalAllocPoint` equals that pool's allocation.
2. Have User A `deposit()` `X` staking tokens; fund `MasterMagpie` with sufficient MGP for rewards.
3. Advance time by `T1`, call `updatePool()`, record `accMGPPerShare_1`.
4. As an unprivileged attacker (no approval/allowance needed on MasterMagpie's side, just token ownership), call `stakingToken.transfer(masterMagpie, Y)` directly (bypassing `deposit`).
5. Advance time by `T2` (same as `T1`), call `updatePool()` again, record `accMGPPerShare_2`.
6. Assert `(accMGPPerShare_2 - accMGPPerShare_1)` for step 5 is strictly less than the increment from step 3 (i.e., `< mgpPerSec * T2 * 1e12 / X`), proving dilution from the donation.
7. Have User A call `multiclaim([stakingToken])`, and assert `mgp.balanceOf(userA)` is less than the "fair" full-supply-based reward. Assert `masterMagpie`'s residual MGP balance corresponding to the undistributed portion has no `UserInfo` path to ever be claimed (sum of all users' claimable computed via `_calMGPReward` across the full staking-token holder set is strictly less than the total MGP emitted for the interval).
8. Assert the attacker's own token balance decreased by `Y` and they received no `UserInfo.amount`/rewards from it, and cannot withdraw it (calling `withdraw` for `Y` reverts `WithdrawAmountExceedsStaked`), confirming the donated principal and diluted yield are both permanently stuck.

### Citations

**File:** rewards/MasterMagpie.sol (L384-388)
```text
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

**File:** rewards/MasterMagpie.sol (L508-514)
```text
    function _withdraw(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        _harvestAndUnstake(_stakingToken, _account, _amount, _isVlMgp);

        if (!_isVlMgp)
            IERC20(tokenToPoolInfo[_stakingToken].stakingToken).safeTransfer(address(msg.sender), _amount);
        emit Withdraw(_account, _stakingToken, _amount);
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
