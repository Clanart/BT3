Confirmed: `MGP` is a fixed-supply pre-minted token given to a recipient (likely `MasterMagpie` or a distributor funding it) at construction [1](#0-0) , and `MasterMagpie` only ever moves already-held MGP out via `safeTransfer`/`safeApprove` in `_sendMGP`/`_sendMGPForVlMGPPool`/`_sendVlMGPFor` [2](#0-1) , it never mints. This confirms the described mechanism results in real, permanently stranded MGP rather than a purely cosmetic accounting bug.

### Title
lpSupply inflation via direct ERC20 donation dilutes `accMGPPerShare`, permanently stranding unclaimed MGP yield - (File: rewards/MasterMagpie.sol)

### Summary
`_calLpSupply()` uses `IERC20(_stakingToken).balanceOf(address(this))` as the reward-share denominator for any pool that is not `vlmgp` or `mWomSV` [3](#0-2) . An attacker can transfer the staking-token asset directly to `MasterMagpie` (bypassing `deposit`), inflating this balance without incrementing any `UserInfo.amount`, causing `updatePool()` to divide the per-interval `mgpReward` by an artificially large `lpSupply` [4](#0-3) . This permanently strands the undistributed portion of MGP that was already earmarked/held for that pool's stream, since no `UserInfo` entry corresponds to the donated balance.

### Finding Description
- `updatePool(_stakingToken)` computes `mgpReward = multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint` and adds `mgpReward * 1e12 / lpSupply` to `pool.accMGPPerShare` [5](#0-4) .
- `lpSupply` for a normal pool is simply `_stakingToken.balanceOf(address(this))` [3](#0-2) , with no reconciliation against the sum of `userInfo[...].amount` that legitimately entered through `_deposit` [6](#0-5) .
- An unprivileged attacker (or anyone) can call `IERC20(_stakingToken).transfer(masterMagpie, amount)` directly — no `deposit()` call is required, so no `UserInfo.amount` is credited for that balance. `deposit()` itself is not strictly required to reach the bug; a plain ERC20 transfer to the contract is sufficient, since `deposit()`'s own `safeTransferFrom` path additionally credits `user.amount`, but the raw donation path does not.
- Once the pool's real `lpSupply` denominator is inflated above the sum of legitimate `UserInfo.amount`, every subsequent `accMGPPerShare` increment under-credits legitimate stakers relative to the MGP that was actually allocated to the pool for that interval (`mgpPerSec * pool.allocPoint / totalAllocPoint * multiplier`). The shortfall is never recoverable by any user because MGP is a fixed pre-minted supply (not minted on claim) [1](#0-0) , and `_sendMGP`/`_sendMGPForVlMGPPool`/`_sendVlMGPFor` only ever transfer out MGP proportional to `accMGPPerShare * user.amount` [2](#0-1) , so the diluted portion sits unclaimed and unclaimable in the contract forever.
- No modifier (`nonReentrant`, `whenNotPaused`) or accounting check protects against this because the contract has no way to distinguish "staked, credited" balance from "directly transferred, uncredited" balance for non-`vlmgp`/non-`mWomSV` pools.

### Impact Explanation
For the affected pool, the effective `accMGPPerShare` growth rate permanently understates the true MGP emission rate whenever `balanceOf(this) > sum(UserInfo.amount)`. The gap between minted/allocated MGP for that stream and what stakers can actually claim is stuck in the contract indefinitely (until/unless an owner-privileged rescue path exists, which is out of scope for an unprivileged actor and not present in this contract). This matches "Permanent freezing of unclaimed yield" (High), because real MGP value becomes unclaimable by any user through the intended and only exposed interfaces (`multiclaim*` family, which read from `unClaimedMgp`/`accMGPPerShare`/`user.amount`).

### Likelihood Explanation
This requires only owning/acquiring some units of the pool's registered staking token (attacker's own ERC20 or a purchasable LP/receipt token) and sending a plain `transfer()` to the `MasterMagpie` address — no special role, no flash loan, no reentrancy. It is fully repeatable for any pool where `_stakingToken` is not `vlmgp`/`mWomSV`, and the "pool is the sole allocPoint holder" precondition described in the prompt only maximizes the damage/speed but is not required for the bug to exist — the dilution happens for any pool proportionally to its allocPoint share. Feasibility is high; capital required is just enough tokens to move the balance noticeably relative to existing legitimate stake.

### Recommendation
Track staked-and-credited supply explicitly instead of relying on `balanceOf(address(this))`. Maintain a `pool.totalStaked` (or equivalent) state variable incremented/decremented only inside `_deposit`/`_withdraw`/`_harvestAndUnstake`/`emergencyWithdraw`, and use that value as the denominator in `updatePool()` and `_calMGPReward()` instead of `_calLpSupply()`'s raw `balanceOf` for non-`vlmgp`/non-`mWomSV` pools. This makes `accMGPPerShare` reconcile exactly with the sum of `UserInfo.amount`, immune to direct-transfer donations.

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, an MGP token pre-funded to the contract, and one ERC20 mock as `_stakingToken`.
2. Call `add()` (as owner/poolManager, admin-only setup step, not part of attacker capability) to register the pool with `allocPoint = 100` as the only pool with non-zero allocation, so `totalAllocPoint == pool.allocPoint`.
3. Legit user `A` calls `deposit(stakingToken, 100e18)` — assert `userInfo[stakingToken][A].amount == 100e18` and `_calLpSupply(stakingToken) == 100e18`.
4. Attacker (no role) calls `stakingToken.transfer(masterMagpie, 900e18)` directly (no `deposit()`).
5. Warp time forward by `T` seconds; call `updatePool(stakingToken)`.
6. Assert `_calLpSupply(stakingToken) == 1000e18` while `userInfo[stakingToken][A].amount` is still `100e18` — the two diverge, confirming the broken invariant.
7. Have `A` call `multiclaim([stakingToken])` and record MGP received; compare against `mgpPerSec * T` (the amount that should have been fully distributable since `A` was the only real staker and pool had 100% of `totalAllocPoint`). Assert `A`'s received MGP `< mgpPerSec * T`, and the shortfall remains stuck as MGP balance in `MasterMagpie` with no `UserInfo` claim path to it — demonstrating permanent freezing of unclaimed yield.

### Citations

**File:** Mgp.sol (L9-12)
```text
contract MGP is ERC20('Magpie Token', 'MGP'), ERC20Permit('Magpie Token') {
    constructor(address _receipient, uint256 _totalSupply) {
        _mint(_receipient, _totalSupply);
    }
```

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

**File:** rewards/MasterMagpie.sol (L638-657)
```text
    function _sendMGPForVlMGPPool(address _account, address _receiver, uint256 _amount) internal {
        address vlMGPRewarder = tokenToPoolInfo[address(vlmgp)].rewarder;
        IERC20(mgp).safeApprove(vlMGPRewarder, _amount);
        IvlmgpPBaseRewarder(vlMGPRewarder).queueMGP(_amount, _account, _receiver);

        emit HarvestMGP(_account, _receiver, _amount, false);
    }

    function _sendMGP(address _account, address _receiver, uint256 _amount) internal {
        IERC20(mgp).safeTransfer(_receiver, _amount);

        emit HarvestMGP(_account, _receiver, _amount, false);
    }

    function _sendVlMGPFor(address _account, address _receiver, uint256 _amount) internal {
        IERC20(mgp).safeApprove(address(vlmgp), _amount);
        vlmgp.lockFor(_amount, _account);

        emit HarvestMGP(_account, _receiver, _amount, true);
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
