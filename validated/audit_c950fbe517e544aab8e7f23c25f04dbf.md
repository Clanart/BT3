### Title
`multiclaimSpec` locks in donation-inflated `_calLpSupply()`, permanently diluting/freezing MGP yield for legitimate stakers - (File: rewards/MasterMagpie.sol)

### Summary
`_calLpSupply()` returns `IERC20(_stakingToken).balanceOf(address(this))` for any pool that isn't the vlMGP or mWomSV locker, instead of an internally tracked "total credited stake" figure. Anyone can send raw staking-receipt tokens directly to `MasterMagpie` (no `deposit()` call, no `UserInfo.amount` credit) and then call the permissionless `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`, which internally calls `updatePool(_stakingToken)` for every token address the caller supplies — even tokens/pools the caller has zero stake in. This locks in an `accMGPPerShare` computed against the inflated balance, permanently reducing the MGP each legitimate staker's `UserInfo.amount` can later claim for that interval.

### Finding Description
`_calLpSupply()`:
```solidity
function _calLpSupply(address _stakingToken) internal view returns (uint256) {
    if (_stakingToken == address(vlmgp)) return IERC20(address(vlmgp)).totalSupply();
    if (_stakingToken == address(mWomSV)) return IERC20(address(mWomSV)).totalSupply();
    return IERC20(_stakingToken).balanceOf(address(this));
}
``` [1](#0-0) 

is used as the denominator in `updatePool()`:
```solidity
uint256 lpSupply = _calLpSupply(_stakingToken);
...
pool.accMGPPerShare = pool.accMGPPerShare + ((mgpReward * 1e12) / lpSupply);
pool.lastRewardTimestamp = block.timestamp;
``` [2](#0-1) 

`mgpReward` is computed purely from elapsed time, `mgpPerSec`, and `allocPoint/totalAllocPoint`, and is not itself scaled up if `lpSupply` is inflated. There is no internal `totalStaked`/`totalSupply()` accounting maintained inside `MasterMagpie` for regular pools — the contract trusts the raw ERC20 `balanceOf`. Any account (unprivileged EOA or contract) can transfer the staking token directly to `MasterMagpie` via a plain ERC20 `transfer`, bypassing `_deposit()` entirely so no `UserInfo.amount` is credited to anyone.

`multiclaimSpec` reaches `updatePool` for attacker-chosen tokens without requiring the caller to hold any stake in them:
```solidity
function multiclaimSpec(address[] calldata _stakingTokens, address[][] memory _rewardTokens)
    external whenNotPaused
{
    _multiClaim(_stakingTokens, msg.sender, msg.sender, _rewardTokens);
}
...
for (uint256 i = 0; i < length; ++i) {
    address _stakingToken = _stakingTokens[i];
    UserInfo storage user = userInfo[_stakingToken][_user];
    updatePool(_stakingToken);
    ...
}
``` [3](#0-2) [4](#0-3) 

Once `updatePool` runs with the donation-inflated `lpSupply`, `accMGPPerShare` is permanently understated versus what it should be had the denominator only reflected credited `UserInfo.amount`. Because `accMGPPerShare` only ever increases and past intervals are never re-computed, the reward corresponding to the "gap" between real staked amount and inflated balance is permanently lost — it is neither claimable by the attacker (who has no `UserInfo.amount` in that pool) nor by legitimate stakers (whose share is calculated off the diluted `accMGPPerShare`).

No modifier, `nonReentrant`, or `whenNotPaused` check prevents this, because the donation itself is a plain token transfer external to the contract's deposit path, and `updatePool`/`multiclaimSpec` are intentionally public/permissionless. The effect is more pronounced for low-decimal receipt tokens where `10**decimals` is small relative to `totalStaked()`, meaning a comparatively small donation produces a large relative inflation of `lpSupply`.

### Impact Explanation
This permanently reduces (freezes) the MGP yield that should have accrued to legitimate stakers of the targeted pool for every interval in which the diluted `accMGPPerShare` is locked in via `updatePool`. Since `accMGPPerShare` is monotonic and never corrected, the lost yield can never be recovered by any party — matching the "High - Permanent freezing of unclaimed yield" impact class. It is a griefing-style attack: the attacker does not profit from the donated tokens (they are not credited via `UserInfo.amount` and cannot be withdrawn), but they inflict a real, permanent, and repeatable loss on other users' claimable MGP.

### Likelihood Explanation
- No privileged role is required; only a plain ERC20 `transfer` of the staking token to `MasterMagpie` and a call to a permissionless function (`multiclaimSpec`, or equally `updatePool`/`massUpdatePools`/`deposit`/`withdraw`, all of which invoke `updatePool`).
- Capital needed is minimal — even a small donation, especially of a low-decimal receipt token, meaningfully skews `lpSupply` for pools with modest `totalStaked()`.
- Fully repeatable across any registered pool that is not vlMGP/mWomSV, at any time, by any address.
- The exploit does not depend on any admin/governance path.

### Recommendation
Replace `balanceOf(address(this))` in `_calLpSupply()` for the default branch with an internally tracked total (e.g., a `totalStaked[_stakingToken]` accumulator incremented in `_deposit`/`depositFor` and decremented in `_withdraw`/`withdrawFor`/`emergencyWithdraw`), so pool reward math is driven only by credited stake and is immune to direct token donations.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `MasterMagpie`, register a pool with a mock low-decimal ERC20 staking token (e.g., 6 decimals), set `mgpPerSec` and `allocPoint`.
2. Victim calls `deposit(stakingToken, X)` to establish `UserInfo.amount` and a baseline `_calLpSupply()==X`.
3. Advance time; record victim's `pendingMGP` via `_calMGPReward`/`allPendingTokens`.
4. Attacker (unprivileged, no prior stake) directly `transfer`s a large amount `Y` of the staking token to `MasterMagpie` (no `deposit()` call), so `balanceOf(MasterMagpie) == X + Y` while `userInfo[stakingToken][attacker].amount == 0`.
5. Attacker calls `multiclaimSpec([stakingToken], [[]])`, triggering `updatePool(stakingToken)` with `lpSupply == X + Y`.
6. Assert: `pool.accMGPPerShare` after step 5 is lower than it would have been with `lpSupply == X`; assert victim's newly accrued `pendingMGP` for the elapsed interval is reduced proportionally to `X/(X+Y)`; assert `userInfo[stakingToken][victim].amount` is unchanged but no longer reconciles with `_calLpSupply(stakingToken)`; assert the "lost" MGP for that interval is unclaimed by any account (attacker's `UserInfo.amount` remains 0, so `claimableMgp` for attacker is 0), demonstrating permanent freezing of that yield.

### Citations

**File:** rewards/MasterMagpie.sol (L374-396)
```text
    function updatePool(address _stakingToken) public whenNotPaused {
        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        if (block.timestamp <= pool.lastRewardTimestamp || totalAllocPoint == 0) {
            return;
        }
        uint256 lpSupply = _calLpSupply(_stakingToken);
        if (lpSupply == 0) {
            pool.lastRewardTimestamp = block.timestamp;
            return;
        }        
        uint256 multiplier = block.timestamp - pool.lastRewardTimestamp;
        uint256 mgpReward = (multiplier * mgpPerSec * pool.allocPoint) / totalAllocPoint;
        
        pool.accMGPPerShare = pool.accMGPPerShare + ((mgpReward * 1e12) / lpSupply);
        pool.lastRewardTimestamp = block.timestamp;

        emit UpdatePool(
            _stakingToken,
            pool.lastRewardTimestamp,
            lpSupply,
            pool.accMGPPerShare
        );
    }    
```

**File:** rewards/MasterMagpie.sol (L406-410)
```text
    function multiclaimSpec(address[] calldata _stakingTokens, address[][] memory _rewardTokens)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, msg.sender, msg.sender, _rewardTokens);
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
