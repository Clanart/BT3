### Title
Unauthorized Third Party Can Force Reward Claims for Arbitrary Accounts via `multiclaimFor` - (File: `rewards/MasterMagpie.sol`)

### Summary
`MasterMagpie.multiclaimFor()` accepts an arbitrary `_account` parameter and never verifies that the caller is authorized to act on behalf of that account, unlike the sibling function `multiclaimOnBehalf()` which is properly gated by `_onlyCompounder`. This mirrors the reported bug class: a function accepts an account identifier distinct from the actual transaction sender/caller context, and the code fails to verify the two match before mutating that account's on-chain state.

### Finding Description
`multiclaimFor` is `external` with no access-control modifier, taking any `_account` address supplied by the caller: [1](#0-0) 

Compare this with `multiclaimOnBehalf`, which performs the equivalent operation but is restricted with `_onlyCompounder`: [2](#0-1) 

Internally, `_multiClaim` uses `_user` (the caller-supplied `_account`) to zero out `unClaimedMgp`, reset `user.rewardDebt`, and route the pending MGP reward according to pool type: [3](#0-2) 

For staking pools that are neither `vlmgp` nor flagged `MPGRewardPool` (the "default pool" case, e.g. ordinary LP/receipt-token staking pools), the accrued MGP reward is routed to `_sendVlMGPFor`, which locks the MGP into `vlmgp` on behalf of `_user`: [4](#0-3) 

Because `multiclaimFor` never checks `msg.sender == _account` (or any equivalent authorization), any unprivileged wallet can call it and force this claim-and-lock sequence to execute for any other user's staking positions at a time of the caller's choosing.

### Impact Explanation
While the `_receiver` in this path equals `_account` (so tokens are not diverted to the attacker), the attacker fully controls *when* another user's pending MGP rewards from default pools get irrevocably converted into a `vlmgp` lock. This forcibly moves what would otherwise remain claimable/liquid MGP into a vote-escrow lock without the account owner's consent, at a timing the account owner did not choose, and zeroes their `rewardDebt`/`unClaimedMgp` accounting in the process. Since `vlmgp` locks are vote-escrow positions with lock durations well beyond 24 hours, this constitutes an involuntary, protocol-triggered freeze of the victim's yield/reward tokens that the account holder did not request.

### Likelihood Explanation
The function is `external`, unauthenticated beyond the implicit (but unenforced) assumption that `_account` should equal the caller, and is reachable directly by any ordinary wallet with no privileged role required — matching the "unprivileged-wallet analog" and "strongest reachable contracts path from an ordinary wallet's transaction" criteria.

### Recommendation
Add an authorization check in `multiclaimFor` analogous to the recommendation in the source report — verify that `msg.sender == _account`, or remove the function/require it go through the same `_onlyCompounder`-style gating as `multiclaimOnBehalf`, so that a user's reward-claim/lock timing cannot be triggered by a third party.

### Proof of Concept
1. Victim stakes an LP/receipt token in a "default" (non-vlmgp, non-`MPGRewardPool`) pool via `MasterMagpie.deposit()`, accruing pending MGP rewards over time.
2. Attacker (any unprivileged EOA) calls `MasterMagpie.multiclaimFor(stakingTokens, rewardTokens, victimAddress)` — note there is no check that `msg.sender == victimAddress`.
3. `_multiClaim` executes with `_user = _receiver = victimAddress`: it zeroes `unClaimedMgp[stakingToken][victim]`, resets `user.rewardDebt`, and calls `_sendVlMGPFor(victim, victim, defaultPoolAmount)`, which locks the victim's pending MGP into `vlmgp` on the victim's behalf.
4. The victim's MGP reward — which they may have wanted to leave unclaimed or claim later under different market/lock conditions — is now locked in `vlmgp` under the attacker's chosen timing, without the victim's transaction or consent, subject to the vote-escrow lock duration. [1](#0-0) [3](#0-2)

### Citations

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

**File:** rewards/MasterMagpie.sol (L652-657)
```text
    function _sendVlMGPFor(address _account, address _receiver, uint256 _amount) internal {
        IERC20(mgp).safeApprove(address(vlmgp), _amount);
        vlmgp.lockFor(_amount, _account);

        emit HarvestMGP(_account, _receiver, _amount, true);
    }
```
