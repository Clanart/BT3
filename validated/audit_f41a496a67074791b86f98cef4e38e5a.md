### Title
Permissionless `multiclaimFor` lets a third party force a victim's default-pool MGP rewards into a locked vlMGP position - ([File: rewards/MasterMagpie.sol])

### Summary
`multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` has no access-control modifier and can be called by anyone for any `_account`, unlike its sibling `multiclaimOnBehalf` which is restricted with `_onlyCompounder`. It routes to `_multiClaim`, which for any staking token that is neither `vlmgp` nor flagged `MPGRewardPool[_stakingToken]` (i.e. "default pool") accumulates `defaultPoolAmount` and calls `_sendVlMGPFor(_user, _receiver, defaultPoolAmount)`, which locks the victim's MGP into vlMGP (`vlmgp.lockFor(_amount, _account)`) instead of transferring it. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`_multiClaim` classifies each staking token's pending MGP into one of three buckets: `vlMGPPoolAmount` (staking token is `vlmgp` itself), `mWOmPoolAmount` (flagged `MPGRewardPool[_stakingToken] == true`), or `defaultPoolAmount` (everything else). [4](#0-3) 

For `defaultPoolAmount`, the contract calls `_sendVlMGPFor(_user, _receiver, defaultPoolAmount)`, which approves and calls `vlmgp.lockFor(_amount, _account)` — locking the MGP for `_user` in vlMGP rather than transferring liquid MGP to the receiver. [5](#0-4) [3](#0-2) 

Because `multiclaimFor` is `external whenNotPaused` with no caller-restriction modifier, and forwards `_account` as both `_user` and `_receiver` into `_multiClaim`, any unprivileged address can pick an arbitrary victim, an arbitrary set of default-pool staking tokens the victim has staked in, and force-harvest+lock the victim's accrued MGP reward into vlMGP at a time of the attacker's choosing — without the victim's consent. [1](#0-0) [6](#0-5) 

This contradicts the documented intent of the codebase's `_For()` naming convention, which the contract's own top-level comment states is meant to be restricted to Magpie's own trusted contracts ("All the ___For() function are function which are supposed to be called by other contract designed by Magpie's team"), and is inconsistent with the analogous `multiclaimOnBehalf`, which enforces `_onlyCompounder`, and with `depositFor`/`withdrawFor`, which enforce `_onlyPoolHelper`. [7](#0-6) [8](#0-7) [9](#0-8) 

Neither `whenNotPaused` nor `nonReentrant` on `_multiClaim` prevent this because they don't check caller identity relative to `_account`; there is no check that `msg.sender == _account` or that `msg.sender` is an authorized contract.

Regarding the invariant about `userInfo[_stakingToken][user].amount` vs `_calLpSupply(_stakingToken)`: the claim path does not touch `user.amount` for the staking token being claimed against — only `user.rewardDebt` is updated — so the staked-amount/LP-supply reconciliation itself is not broken by this call. The actual, confirmed impact is limited to forcing the reward-accounting conversion (claimable MGP → locked vlMGP), not a supply/accounting desync. [10](#0-9) 

I was not able to fully verify the exact vlMGP cooldown/penalty mechanics (`getRewardablePercentWAD`, unlock cooldown duration, penalty schedule) from `VLMGP.sol` within the available tool budget — the file exists and contains the relevant logic (`lockFor`, cooldown/unlock functions), but its full contents were not retrieved before the iteration budget ran out, so the "at least 24 hours" freezing duration and "mid-cooldown ⇒ getRewardablePercentWAD stays 1e18" precondition stated in the question could not be independently confirmed line-by-line.

### Impact Explanation
Regardless of the exact vlMGP cooldown parameters, the core issue is real: `multiclaimFor` lacks the access control that its sibling functions in the same file enforce, and this allows an unprivileged third party to force the conversion of a victim's unclaimed, liquid MGP reward entitlement into a vlMGP-locked position at a time the victim did not choose. Since vlMGP is a lock/cooldown/penalty-bearing token (per `interfaces/ILocker.sol` and `VLMGP.sol`), this forces the victim's funds into an illiquid state they must go through an unlock/cooldown process (and potentially a penalty) to exit — matching "Temporary freezing of funds" impact class, contingent on the vlMGP unlock delay actually being ≥24 hours (plausible for a vote-locked governance/reward token but not independently confirmed here).

### Likelihood Explanation
The attack requires no special privileges or capital: any staking-token address and any victim address that has default-pool positions with pending MGP reward can be targeted; the attacker only needs to know the victim's address and staking-token list, both public/observable on-chain. The call is fully permissionless (`multiclaimFor` has no ownership/role modifier), repeatable, and can be front-run/triggered at will by the attacker, making it a low-cost, easily repeatable griefing vector against every default-pool staker.

### Recommendation
Restrict `multiclaimFor` similarly to `multiclaimOnBehalf`/`depositFor`/`withdrawFor` — e.g. require `msg.sender == _account` or an explicit allow-list/approval mechanism (victim opts in to letting specific relayers claim on their behalf) before allowing MGP to be locked into vlMGP for `_account`. At minimum, gate the `_sendVlMGPFor` forced-lock behavior so it can only be triggered by the reward owner themselves or an address the owner has explicitly authorized (similar to an ERC-20 allowance pattern), rather than being open to any caller.

### Proof of Concept
1. Deploy/fork with `MasterMagpie`, `VLMGP`, and a default-pool staking token (not `vlmgp`, not flagged in `MPGRewardPool`).
2. Victim deposits into the default pool and accrues pending MGP reward over time (`updatePool` advances `accMGPPerShare`).
3. Confirm the victim has not called `multiclaim*` and currently holds an unclaimed/liquid MGP entitlement (`_calMGPReward` > 0), and is not currently staked/locked in vlMGP (baseline: victim's vlMGP balance = 0, MGP balance = 0).
4. From an unrelated attacker EOA, call `multiclaimFor([defaultPoolStakingToken], [[]], victim)`.
5. Assert:
   - `IERC20(mgp).balanceOf(victim) == 0` (victim received no liquid MGP).
   - `vlmgp.balanceOf(victim) > 0` / victim now has a locked vlMGP position for the previously-liquid reward amount, confirming `vlmgp.lockFor` was invoked on the victim's behalf without consent.
   - Attempting `vlmgp.withdraw`/unlock as the victim immediately after reverts or is subject to the cooldown period, demonstrating the funds are no longer immediately liquid despite the victim not having chosen to lock them.
6. Compare against the intended safe behavior: `multiclaimOnBehalf` (restricted to `compounder`) and self-called `multiclaimSpec`/`multiclaim`, showing that only `multiclaimFor` (no access control) allows an arbitrary third party to trigger this outcome for another address.

### Citations

**File:** rewards/MasterMagpie.sol (L30-32)
```text
/// @author Magpie Team
/// @notice You can use this contract for depositing MGP, MWOM, and Liquidity Pool tokens.
/// @dev All the ___For() function are function which are supposed to be called by other contract designed by Magpie's team
```

**File:** rewards/MasterMagpie.sol (L352-370)
```text
    function depositFor(
        address _stakingToken,
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyPoolHelper(_stakingToken) nonReentrant {
        _deposit(_stakingToken, _for, _amount, false);
    }

    /// @notice Withdraw staking tokens from Mastser Magpie for a specific user. Can only be called by pool helper
    /// @param _stakingToken Staking token of the pool
    /// @param _amount amount to withdraw   
    /// @param _for address of the user to withdraw for, and also harvested reward will be sent to
    function withdrawFor(
        address _stakingToken,
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyPoolHelper(_stakingToken) nonReentrant {
        _withdraw(_stakingToken, _for, _amount, false);
    }
```

**File:** rewards/MasterMagpie.sol (L412-417)
```text
    /// @notice Claims for each of the pools with specified rewards to claim for each pool
    function multiclaimFor(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, _account, _account, _rewardTokens);
    }
```

**File:** rewards/MasterMagpie.sol (L419-424)
```text
    /// @notice Claims for each of the pools with specified rewards to claim for each pool. ONLY callable by compounder!!!!!!
    function multiclaimOnBehalf(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused _onlyCompounder
    {
        _multiClaim(_stakingTokens, _account, msg.sender, _rewardTokens);
    }
```

**File:** rewards/MasterMagpie.sol (L536-574)
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
```

**File:** rewards/MasterMagpie.sol (L652-657)
```text
    function _sendVlMGPFor(address _account, address _receiver, uint256 _amount) internal {
        IERC20(mgp).safeApprove(address(vlmgp), _amount);
        vlmgp.lockFor(_amount, _account);

        emit HarvestMGP(_account, _receiver, _amount, true);
    }
```
