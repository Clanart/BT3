### Title
`ManualCompound.compound` uses raw `balanceOf` instead of tracked deltas, letting any caller sweep other users' leftover reward tokens - (File: rewards/ManualCompound.sol)

### Summary
`ManualCompound.compound()` is a fully unprivileged, externally callable function (no access control beyond msg.sender being the claimant) that determines how much of each reward token to convert/lock/forward by reading the contract's *total* `balanceOf(address(this))` rather than tracking the amount actually produced by the caller's own claim in that transaction.

### Finding Description
In `compound()`, both the "non-compoundable" forwarding loop and the "compoundable" processing loop compute the amount to act on directly from the token's live balance on the contract: [1](#0-0) [2](#0-1) 

There is no bookkeeping of "amount claimed for this caller in this call" versus "amount already sitting in the contract from a prior interaction." Any residual balance of a reward token held by `ManualCompound` — for example dust left behind by an earlier caller's `IConverter.convertFor`, `ILocker.lockFor`, or `ISimpleHelper.depositFor` call that didn't consume 100% of the approved/transferred amount due to rounding, or reward tokens mistakenly/deliberately sent directly to the contract address — is entirely swept to `msg.sender` of the *next* `compound()` invocation, not returned to whoever actually generated it.

Because `compound()` takes attacker-controlled `_lps`/`_rewards` arrays (which can be empty or minimal) and no modifier restricts frequency or amount, any ordinary wallet can call `compound()` repeatedly (including immediately after observing a large legitimate compound transaction) to claim whatever balance is currently resident in the contract for reward tokens registered via `addReward`, regardless of whether that balance derives from the caller's own claim.

### Impact Explanation
This allows an unprivileged wallet to redirect other users' un-forwarded reward remainders (dust or rounding residue from the conversion/locking pipeline, or accidental transfers) to itself. Over repeated calls/tokens/users, this constitutes direct theft of unclaimed yield that rightfully belonged to prior compounders, and the loss is permanent since the swept tokens are transferred out of the contract with no accounting record of who they belonged to.

### Likelihood Explanation
The function is `external` with no `onlyOwner`/`onlyMasterMagpie`/reentrancy-style delta tracking, and the exploit requires nothing more than calling `compound()` after any prior interaction that leaves dust (a near-guaranteed occurrence with `safeApprove`/`convertFor`/`lockFor`/`depositFor` flows across many tokens and many users). No special timing, front-running, or privileged role is needed — any wallet that notices non-zero token balances at the `ManualCompound` address can call `compound()` to capture them.

### Recommendation
Track the exact amount received per call instead of relying on `balanceOf(address(this))`:
- Capture `balanceOf` before and after `multiclaimOnBehalf`, and only act on the delta attributable to the current claim, per token.
- Alternatively, maintain an internal ledger of undistributed/queued dust per token that only the contract itself can top up and clear deterministically, rather than trusting the live external balance as ground truth.
- Add an owner-only or MasterMagpie-only sweep function for truly stray tokens (with the standard `stakingToken`/reward-token exclusions à la the original `recoverERC20` pattern) instead of implicitly funneling all resident balance to whichever ordinary wallet calls `compound()` next.

### Proof of Concept
1. User A calls `compound()` with a `_lps`/`_rewards` set that claims a reward token `T` through a `convertor` (`IConverter.convertFor`). Suppose the conversion consumes `receivedBalance - d` of `T` and leaves `d` wei of dust in the `ManualCompound` contract (rounding in the converter, or the converter reverts/partially executes leaving `T` behind if `_minRec` isn't met on a downstream step handled outside this call).
2. `T` is registered as compoundable (`compoundableRewards[T] == true`), so it is not swept back to A in the "non-compoundable" loop; the dust `d` simply remains at `address(this)`.
3. Attacker B, who has never earned any of token `T`, calls `compound()` with `_lps = []` / `_rewards = []` (no actual claim). Since `rewardTokensLength` iterates over all registered `rewards` regardless of what was claimed, the loop at [2](#0-1)  reads `receivedBalance = IERC20(T).balanceOf(address(this))`, which is `d` (A's leftover dust), and forwards/converts/locks it to B.
4. B has captured value that rightfully belonged to A's compound operation, with zero cost beyond gas, and no admin or special role was required.

### Citations

**File:** rewards/ManualCompound.sol (L126-138)
```text
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

**File:** rewards/ManualCompound.sol (L139-159)
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
```
