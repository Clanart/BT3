### Title
Full-balance sweep instead of delta-based accounting in `ManualCompound.compound()` allows theft of stuck reward tokens - (File: `rewards/ManualCompound.sol`)

### Summary
`ManualCompound.compound()` forwards to the caller (or to a convertor/locker/helper on the caller's behalf) the **entire current token balance held by the contract**, rather than only the amount that was actually claimed for that specific caller within the same transaction. This is the same root cause identified in the Wido `LibCollateralSwap` report: instead of tracking and moving only the delta amount that belongs to the current interaction, the contract blindly sweeps `balanceOf(address(this))` and hands it to `msg.sender`. Any token balance that ends up resident in the contract for any reason (e.g. a caller mistakenly including/receiving the wrong reward token, dust, or a misdirected transfer) can subsequently be claimed in full by an unrelated, unprivileged caller.

### Finding Description
In `compound()`, two loops operate purely on contract-wide token balances, not on amounts attributable to `msg.sender`'s own claim in the current call: [1](#0-0) 

Loop 1 ("send none compoundable reward back to caller") lets the caller freely choose arbitrary `_rewards[i][j]` token addresses. For any address that is not in `compoundableRewards`, the **full current balance** of that token in the contract is sent to `msg.sender`: [2](#0-1) 

Loop 2, for registered/compoundable rewards, similarly reads `IERC20(_tokenAddress).balanceOf(address(this))` and forwards/approves that entire balance to a convertor, locker, helper, or directly to `msg.sender`: [3](#0-2) 

The claiming step itself, `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)`, only credits the actual entitlement of `msg.sender` for the specified pools/reward tokens via `MasterMagpie._multiClaim` → `BaseRewardPool.getRewards`: [4](#0-3) 

However, nothing ties the amount claimed for `msg.sender` to the amount subsequently swept and paid out — the sweep is based purely on whatever token balance happens to sit in the `ManualCompound` contract at that moment. If any token balance is present in the contract from a source unrelated to the current caller's legitimate claim (e.g., an accidental direct transfer to the contract, or residual balance left from a mis-specified `_rewards` call by another user), the next caller can simply include that token's address as one of their `_rewards[i][j]` entries (loop 1) or, if it is a registered reward token, simply call `compound()` at all (loop 2), and receive the entire balance — regardless of whether they were ever entitled to it. This mirrors exactly the flaw described in the source report: "the contract only re-invests the surplus amount ... and simply transfers all of the ... asset in the contract to the caller."

### Impact Explanation
Any unprivileged wallet can permanently steal reward tokens belonging to other users that end up resident in the `ManualCompound` contract, by simply calling `compound()` with the target token address included in an arbitrary `_lps`/`_rewards` pairing. This is direct theft of unclaimed/stuck yield funds, matching the accepted impact categories (theft of unclaimed yield).

### Likelihood Explanation
The precondition — some token balance being present in `ManualCompound` that isn't atomically swept back to its rightful owner within the same transaction it arrived in — can arise from ordinary user error (as in the original report's example of a user picking the wrong `_rewards` entries) or from any interaction that leaves dust. Once such a balance exists, exploitation requires only a single unprivileged transaction with attacker-chosen calldata, no special timing, race condition, or privileged role needed.

### Recommendation
Replace all `balanceOf(address(this))`-based accounting in `ManualCompound.compound()` with delta accounting: measure the token balance immediately before and after the `multiclaimOnBehalf` call, and only forward/convert/lock the measured delta attributable to the current transaction's claim, instead of the full standing balance. This prevents any pre-existing or extraneous token balance from being swept by an unrelated caller.

### Proof of Concept
1. Some ERC20 token `X` balance ends up in the `ManualCompound` contract (e.g., a user calls `compound()` specifying the wrong `_rewards[i][j] = X` for a pool where `X` is not the intended reward and no matching handling exists, or `X` is accidentally transferred directly to the contract).
2. `X` is not in `compoundableRewards`.
3. An attacker calls `compound(_lps, _rewards, ...)` with any `_lps` entry (even one where the attacker's real entitlement for that pool is 0) and `_rewards[i] = [X]`.
4. `multiclaimOnBehalf` executes normally (claiming 0 or unrelated amounts for the attacker).
5. In loop 1, since `compoundableRewards[X]` is `false`, the code executes `IERC20(X).safeTransfer(msg.sender, IERC20(X).balanceOf(address(this)))`, sending the attacker the entire resident balance of `X`, which belonged to another user's mistaken/stuck transfer. [5](#0-4)

### Citations

**File:** rewards/ManualCompound.sol (L123-138)
```text
    function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
        uint256 rewardTokensLength = rewards.length;        
        IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
        // send none compoundable reward back to caller
        for(uint256 i; i < _lps.length; i++) {
            uint256 rewardLength = _rewards[i].length;
            if (rewardLength > 0) {
                for (uint j; j < rewardLength; j++) {
                    if (!compoundableRewards[_rewards[i][j]]) {
                        uint256 rewardBalance = IERC20(_rewards[i][j]).balanceOf(address(this));
                        if (rewardBalance > 0)
                            IERC20(_rewards[i][j]).safeTransfer(msg.sender, rewardBalance);
                    }
                }
            }
        }
```

**File:** rewards/ManualCompound.sol (L139-160)
```text
        for (uint256 i; i< rewardTokensLength; i++) {
            address _tokenAddress = rewards[i].tokenAddress;
            address _helperAddress = rewards[i].tokenHelper;
            address _convertor = rewards[i].convertor;
            address _locker = rewards[i].locker;
            uint256 receivedBalance = IERC20(_tokenAddress).balanceOf(address(this));

            if (receivedBalance > 0) {
                if (_convertor != address(0)) {
                    IERC20(_tokenAddress).safeApprove(_convertor, receivedBalance);
                    IConverter(_convertor).convertFor(receivedBalance, _convertRatio, _minRec, msg.sender, 2);
                } else if (_locker != address(0) && _lockMgp) {
                    IERC20(_tokenAddress).safeApprove(_locker, receivedBalance);
                    ILocker(_locker).lockFor(receivedBalance, msg.sender);                        
                } else if (_helperAddress != address(0)) { 
                    IERC20(_tokenAddress).safeApprove(_helperAddress, receivedBalance);
                    ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender);
                } else {
                    IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance);
                }
            }
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
