### Title
`ManualCompound.compound` sweeps the entire contract reward-token balance to `msg.sender` instead of the caller's own claimed share - (File: rewards/ManualCompound.sol)

### Summary
`compound()` calls `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)`, which pulls claimable rewards into `ManualCompound` (`address(this)`), not directly to the end user. It then, for every *registered* reward token, reads `IERC20(_tokenAddress).balanceOf(address(this))` — the contract's total balance — and forwards the full amount through `convertFor`/`lockFor`/`depositFor`/`safeTransfer` to `msg.sender`. There is no per-caller accounting of how much of that balance actually belongs to the current caller.

### Finding Description
In `rewards/ManualCompound.sol`: [1](#0-0) 

- Line 125: `multiclaimOnBehalf` claims rewards for `msg.sender` (the account being compounded) from `MasterMagpie`, but the reward tokens land on the `ManualCompound` contract's own balance, since `ManualCompound` itself is the caller of `multiclaimOnBehalf`.
- Lines 139–159: for each protocol-registered reward token, the code reads `receivedBalance = IERC20(_tokenAddress).balanceOf(address(this))` — i.e. the *entire* contract balance of that token, not the delta attributable to the current `msg.sender`'s claim — and immediately converts/locks/deposits/transfers that whole amount to `msg.sender`.
- `_minRec` supplied by `msg.sender` is the only slippage guard on the `convertFor` leg (line 149), and it is checked against the aggregate `receivedBalance`, which can include tokens that belong to a different user.

Because `compound()` has no access control and no restriction on the contents of `_lps`/`_rewards` (they can be empty arrays), any unprivileged account can call `compound([], [], _convertRatio, _minRec, _lockMgp)` immediately after another user's `multiclaimOnBehalf` (whether invoked directly or via that user's own pending `compound()` transaction) has deposited reward tokens into `ManualCompound`'s balance but before that legitimate transaction's conversion/sweep step executes in the same block. The attacker's call will pick up the entire outstanding balance — including the victim's freshly claimed rewards — and route the converted proceeds to itself.

This violates the stated invariant that `balanceOf(address(this))` must be reconciled to "the caller's own share": the contract has no bookkeeping (no per-user pending-reward mapping) that separates one caller's freshly-claimed tokens from another's, so the entire pooled balance is fungible and up for grabs by whoever calls `compound()` next.

### Impact Explanation
An attacker can steal another user's claimed-but-not-yet-swept reward tokens by racing `compound()` calls within the same block (or simply calling `compound()` right after observing a pending `multiclaimOnBehalf`/`compound` transaction in the mempool). This is direct theft of another user's unclaimed/claimed yield, matching Critical - Direct theft of user funds.

### Likelihood Explanation
- Requires no privileged role — `compound()` is `external` with no access modifier.
- Requires observing a pending `multiclaimOnBehalf` (via `compound()`) transaction in the mempool for another user and front-running/back-running it in the same block — a standard MEV/front-running technique requiring no special capital, just gas to submit a transaction with higher priority fee.
- Repeatable for every block in which a victim's claim transaction is pending, and for every reward token registered in the `rewards` array.

### Recommendation
Track and convert only the amount attributable to the current caller's claim, e.g. capture `balanceOf(address(this))` immediately before calling `multiclaimOnBehalf` and again immediately after, then only operate on the delta (`balanceAfter - balanceBefore`) for each reward token, rather than the raw total balance. Additionally, consider adding reentrancy protection and disallowing empty `_lps`/`_rewards` arrays to prevent no-op "harvest sweeps."

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `MasterMagpie`, `ManualCompound`, a mock reward token, and a mock `convertor`/`locker` implementing `IConverter`/`ILocker`.
2. Register the reward token via `addReward`.
3. Simulate User A calling `multiclaimOnBehalf` directly (or via `compound()` with a pending tx) such that N tokens land in `ManualCompound`'s balance intended for User A, but the conversion/transfer step has not yet executed (e.g., pause the block/mine only the claim tx).
4. In the same block, have Attacker call `compound([], [], convertRatio, 0, false)`.
5. Assert:
   - `IERC20(rewardToken).balanceOf(ManualCompound)` drops to 0 after Attacker's call.
   - The converted proceeds (or raw token via `safeTransfer` if no convertor/locker/helper set) are received by Attacker, not User A.
   - When User A's original `compound()`/claim transaction subsequently executes (or attempts to sweep), User A receives 0 tokens despite having earned N.
6. Repeat with `_convertRatio` and `_minRec` at boundary values (0, `DENOMINATOR`, `type(uint256).max`) to confirm the theft succeeds regardless of attacker-supplied slippage parameters, since `_minRec` only bounds slippage on the pooled amount, not ownership.

### Citations

**File:** rewards/ManualCompound.sol (L123-160)
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
