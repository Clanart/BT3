### Title
Any caller of `ManualCompound.compound()` can sweep the entire contract balance of any registered "compoundable" reward token, stealing rewards belonging to other users - ([File: rewards/ManualCompound.sol])

### Summary
`ManualCompound.compound()` is a public, unauthenticated function [1](#0-0)  that, after pulling in a caller-specified claim via `IMasterMagpie(masterMagpie).multiclaimOnBehalf`, distributes rewards using the *entire current token balance held by the contract* rather than the amount actually attributable to that specific caller's claim.

### Finding Description
The first loop only forwards non-compoundable tokens that were explicitly passed in `_rewards`, and does nothing for tokens marked `compoundableRewards[token] == true` [2](#0-1) . The second loop then iterates over the *entire persistent admin-configured `rewards` list* (not limited to what the caller actually claimed in `_lps`/`_rewards`) and for each entry computes:
```solidity
uint256 receivedBalance = IERC20(_tokenAddress).balanceOf(address(this));
```
and unconditionally forwards/converts/locks that whole balance to `msg.sender` [3](#0-2) .

Because this reads `balanceOf(address(this))` instead of tracking the delta actually received from the caller's own `multiclaimOnBehalf` call, any residual balance of a compoundable reward token sitting in the contract — from rounding dust in `_provisionReward`/`BaseRewardPoolV2` reward math [4](#0-3) , from `SmartWomConvert`/other converters leaving small remainders, or from any other party's rewards that have not yet fully cleared — becomes payable to whoever calls `compound()` next, even with a trivial/empty claim (`_lps = []`, `_rewards = []`), since `multiclaimOnBehalf` with empty arrays is effectively a no-op and does not gate the second loop.

This is the same root-cause pattern as the reference report: tokens are pulled into an intermediary contract, but the code that "refunds"/distributes them to the caller keys off the contract's ambient balance rather than the amount actually owed to that specific caller, allowing an unrelated actor to claim funds that were never theirs to begin with.

### Impact Explanation
Any leftover compoundable-reward-token balance in `ManualCompound` (dust, timing gaps between multi-step operations, or another user's yet-unprocessed rewards) can be swept in full by any attacker with a cheap, parameter-free call to `compound()`. This is a direct theft of user/protocol reward funds held by the contract, satisfying the "direct theft of user funds" bar.

### Likelihood Explanation
`compound()` has no access control beyond what any EOA can trigger, and the exploit requires no privileged role — only that any non-zero balance of a registered reward token exist in the contract at call time (a condition the attacker can also watch for via mempool/state monitoring, or induce via small dust-creating interactions). This makes the vulnerability practically reachable by an ordinary wallet.

### Recommendation
Track the amount of each reward token actually received for the current caller's claim (e.g., via a before/after balance diff scoped to the `multiclaimOnBehalf` call within `compound()`, or by having `multiclaimOnBehalf` return per-token amounts), and only distribute that delta to `msg.sender` instead of using `IERC20(_tokenAddress).balanceOf(address(this))` for the whole persistent `rewards` list.

### Proof of Concept
1. Assume `ManualCompound` currently holds a nonzero balance of a compoundable reward token `T` (registered via `addReward`), left over from normal dust/rounding in a prior legitimate `compound()`/reward-distribution flow, or from another user's rewards not yet fully processed.
2. Attacker calls `compound([], [], 0, 0, false)` with empty `_lps`/`_rewards` arrays.
3. `multiclaimOnBehalf([], [], attacker)` on `MasterMagpie` is effectively a no-op (loop over zero-length arrays) [5](#0-4) .
4. The first loop in `compound()` does nothing since `_lps.length == 0`.
5. The second loop iterates over the full `rewards` array and, for token `T`, reads `IERC20(T).balanceOf(address(this))` (the residual balance, not attacker's own claim) and transfers/converts/locks it entirely to the attacker [3](#0-2) .
6. The attacker has stolen reward tokens that belonged to the contract/other users without contributing any legitimate claim.

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

**File:** rewards/MasterMagpie.sol (L420-424)
```text
    function multiclaimOnBehalf(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused _onlyCompounder
    {
        _multiClaim(_stakingTokens, _account, msg.sender, _rewardTokens);
    }
```
