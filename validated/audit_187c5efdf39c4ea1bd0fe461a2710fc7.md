### Title
`_multiClaim` computes referral commission on locked (vlMGP-locked) MGP as if it were liquid, causing ReferralStorage insolvency - ([File: rewards/MasterMagpie.sol])

### Summary
`MasterMagpie._multiClaim` sums `vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount` into `totalReward` and passes the whole sum to `IReferralStorage(referral).trigger(_user, totalReward)`, even though `defaultPoolAmount` is never sent as liquid MGP — it is locked via `vlmgp.lockFor(...)` inside `_sendVlMGPFor`, and `vlMGPPoolAmount` is queued into the vlMGP rewarder via `_sendMGPForVlMGPPool`, not transferred out as free MGP. Only `mWOmPoolAmount` is actually paid out as liquid MGP via `_sendMGP`. Since `ReferralStorage.trigger` simply increments `rewardAmount` (payable later in liquid MGP via `claimReward`) proportionally to the full `_amount` it receives, commissions accrue on amounts that were never liquid, letting accrued referral obligations exceed the MGP actually available/backed for referral payouts.

### Finding Description
In `_multiClaim` [1](#0-0) :
- `defaultPoolAmount` is routed through `_sendVlMGPFor`, which approves and calls `vlmgp.lockFor(_amount, _account)` — the MGP is locked into vlMGP, not handed to the user/receiver as spendable MGP [2](#0-1) .
- `vlMGPPoolAmount` is routed through `_sendMGPForVlMGPPool`, which approves and calls `IvlmgpPBaseRewarder(vlMGPRewarder).queueMGP(...)` — again not a direct liquid transfer to the receiver [3](#0-2) .
- Only `mWOmPoolAmount` becomes truly liquid via `_sendMGP`, which does `IERC20(mgp).safeTransfer(_receiver, _amount)` [4](#0-3) .

Despite this, `totalReward = vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount` is computed and forwarded unconditionally to `referral.trigger(_user, totalReward)` [5](#0-4) .

In `ReferralStorage.trigger`, the referrer and referee reward amounts are computed as percentages of this full `_amount` and simply added to `rewardAmount` bookkeeping [6](#0-5) , with no linkage to any actual MGP inflow into `ReferralStorage`. When the referrer eventually calls `claimReward()`, real liquid MGP is transferred out of `ReferralStorage`'s own balance: `MGP.safeTransfer(msg.sender, userInfo.rewardAmount)` [7](#0-6) .

There is no mechanism in `trigger` or `_multiClaim` that funds `ReferralStorage` with MGP proportional to `totalReward`; the contract relies on its pre-funded `MGP` balance (`IERC20 public MGP`) to satisfy `rewardAmount` claims [8](#0-7) . Because `defaultPoolAmount` (locked) and `vlMGPPoolAmount` (queued, not free) are counted the same as `mWOmPoolAmount` (actually liquid), every claim that includes locked/default-pool rewards inflates `rewardAmount` bookkeeping beyond what is ever backed by liquid MGP flow, eventually allowing accrued referral claims to exceed the MGP reserve backed by genuinely liquid distributions — draining `ReferralStorage`'s MGP balance beyond what its own liquid-reward accounting justifies.

No existing modifier or check (`nonReentrant` on `_multiClaim`, `_onlyMasterMagpie` on `trigger`) prevents this because the bug is purely an accounting/amount-classification bug, not an access-control or reentrancy issue.

### Impact Explanation
This is a protocol-insolvency class issue: `ReferralStorage`'s MGP balance can be depleted by commission claims that were never backed by liquid MGP outflow from `MasterMagpie`, because locked (`defaultPoolAmount`) and rewarder-queued (`vlMGPPoolAmount`) reward tranches are counted toward referral commission on par with genuinely liquid (`mWOmPoolAmount`) rewards. Over time and across many referees claiming default-pool (locked) rewards, the sum of `rewardAmount` owed to referrers/referees in `ReferralStorage` can exceed the MGP that was ever actually transferred liquidly, so honoring `claimReward()` for all parties becomes impossible — a direct insolvency/undercollateralization of the referral reward pool, potentially draining MGP that was meant to back other legitimate (liquid-reward-derived) referral claims.

### Likelihood Explanation
No privileged role is required. Any unprivileged user can register as a referrer via `registerCode`, have victims call `useCode` to link to them, and then any victim staking in a default (non-vlMGP, non-MPGRewardPool) pool who claims via `multiclaim`/`multiclaimSpec`/`multiclaimFor` will trigger this miscount automatically, since `defaultPoolAmount` claims always go through the default path in `_multiClaim`. This requires zero special capital or timing — it happens on ordinary, expected claim behavior across the pool that most stakers use (`defaultPoolAmount` is the default branch for staking tokens that are neither vlMGP nor flagged `MPGRewardPool`). It's fully repeatable on every claim cycle.

### Recommendation
Only include amounts that are genuinely paid out as liquid MGP (i.e., `mWOmPoolAmount`, sent via `_sendMGP`) in the `totalReward` passed to `IReferralStorage.trigger`. If referral commissions on locked/vlMGP-routed rewards are intended to be supported, `ReferralStorage` should receive/lock a corresponding backing amount (e.g., via a proportional transfer from `MasterMagpie` at `trigger` time, or a separate accounting/vesting path so that `rewardAmount` accrual is always backed 1:1 by actual MGP held by `ReferralStorage`).

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, `ReferralStorage`, `vlMGP`, and a default pool (non-vlMGP, non-`MPGRewardPool`) plus an mWOM-style pool (`MPGRewardPool[token] = true`).
2. Register `referrer` via `registerCode`; have `victim` call `useCode(code)` to link to `referrer`.
3. Fund `victim` stake in the default pool only, accrue rewards, then have `victim` call `multiclaim` — this routes the full amount through `_sendVlMGPFor` (locked) yet still calls `referral.trigger(victim, defaultPoolAmount)`.
4. Track: (a) cumulative liquid MGP ever transferred out by `_sendMGP` calls (should be zero in this scenario), and (b) `ReferralStorage.userInfos(referrer).rewardAmount` / `userInfos(victim).rewardAmount` after `trigger`.
5. Assert that `referrer`'s (and `victim`'s) accrued `rewardAmount` in `ReferralStorage` is > 0 even though cumulative liquid MGP transferred by `_sendMGP` is 0, demonstrating referral commission accrual with zero underlying liquid-MGP backing.
6. Fund `ReferralStorage` with only the MGP amount that corresponds to actually-liquid (`mWOmPoolAmount`-derived) referral rewards, then have `referrer` call `claimReward()` for the inflated `rewardAmount` and show the transfer either reverts (insufficient balance) or drains MGP intended to back other legitimate liquid-reward claims — proving the backing invariant (`sum(rewardAmount owed) <= MGP actually liquid-flow-backed`) is violated.

### Citations

**File:** rewards/MasterMagpie.sol (L536-581)
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

        if (vlMGPPoolAmount > 0) {
            _sendMGPForVlMGPPool(_user, _receiver, vlMGPPoolAmount);
        }

        if (mWOmPoolAmount > 0) {
            _sendMGP(_user, _receiver, mWOmPoolAmount);
        }

        if (defaultPoolAmount > 0) {
            _sendVlMGPFor(_user, _receiver, defaultPoolAmount);
        }

        uint256 totalReward = vlMGPPoolAmount + mWOmPoolAmount + defaultPoolAmount;

        if (totalReward > 0 && referral != address(0)) {
            IReferralStorage(referral).trigger(_user, totalReward);
        }
    }
```

**File:** rewards/MasterMagpie.sol (L638-644)
```text
    function _sendMGPForVlMGPPool(address _account, address _receiver, uint256 _amount) internal {
        address vlMGPRewarder = tokenToPoolInfo[address(vlmgp)].rewarder;
        IERC20(mgp).safeApprove(vlMGPRewarder, _amount);
        IvlmgpPBaseRewarder(vlMGPRewarder).queueMGP(_amount, _account, _receiver);

        emit HarvestMGP(_account, _receiver, _amount, false);
    }
```

**File:** rewards/MasterMagpie.sol (L646-650)
```text
    function _sendMGP(address _account, address _receiver, uint256 _amount) internal {
        IERC20(mgp).safeTransfer(_receiver, _amount);

        emit HarvestMGP(_account, _receiver, _amount, false);
    }
```

**File:** rewards/MasterMagpie.sol (L652-657)
```text
    function _sendVlMGPFor(address _account, address _receiver, uint256 _amount) internal {
        IERC20(mgp).safeApprove(address(vlmgp), _amount);
        vlmgp.lockFor(_amount, _account);

        emit HarvestMGP(_account, _receiver, _amount, true);
    }
```

**File:** rewards/ReferralStorage.sol (L39-41)
```text
    address public vlMGP;
    IERC20 public MGP;
    address public masterMagpie;
```

**File:** rewards/ReferralStorage.sol (L158-168)
```text
    function claimReward() external {
        UserInfo storage userInfo = userInfos[msg.sender];

        uint256 rewardAmount = userInfo.rewardAmount;
          if (rewardAmount == 0) revert InsufficientRewardBalance(); 

        MGP.safeTransfer(msg.sender, userInfo.rewardAmount);

        emit RewardClaimed(msg.sender, userInfo.rewardAmount);
        userInfo.rewardAmount = 0;
    }
```

**File:** rewards/ReferralStorage.sol (L173-195)
```text
    function trigger(address _referee, uint256 _amount) external _onlyMasterMagpie {
        UserInfo storage refereeInfo = userInfos[_referee];
        address _referer = myReferer[_referee];

        if (_referer == address(0))
            return;

        UserInfo storage refererInfo = userInfos[_referer];
        uint256 tierId = userInfos[_referer].tier;
        uint256 basic = tiers[tierId].rewardPercentage;
        uint256 boostesd = _calBoosted(_referer);

        uint256 refererPercentage = (basic + boostesd) * (DENOMINATOR - sharePercent)  / DENOMINATOR;
        uint256 refereePercentage = (basic + boostesd) *  sharePercent / DENOMINATOR;
        uint256 refererAmount = _amount * refererPercentage / DENOMINATOR;
        uint256 refereeAmount = _amount * refereePercentage / DENOMINATOR;

        refererInfo.rewardAmount += refererAmount;
        refereeInfo.rewardAmount += refereeAmount;

        emit RefererRewardHarvested(_referer, refererAmount);
        emit RefereeRewardHarvested(_referee, refereeAmount);
    }
```
