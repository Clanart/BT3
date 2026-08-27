## Finding: `ManualCompound` distributes based on raw token balance instead of tracked claim amount, allowing theft of other users' unclaimed/leftover reward dust

### Title
Miscalculation of distributed rewards via `balanceOf(address(this))` in `ManualCompound.compound` - (File: `rewards/ManualCompound.sol`)

### Summary
`ManualCompound.compound()` reads the full ERC20 balance held by the contract to determine what to convert/lock/deposit/transfer to the calling user, rather than tracking the amount actually delivered by the specific `multiclaimOnBehalf` call for that user. This mirrors the RevenueHandler bug class: any token balance left over in the contract from a previous, imperfectly-swept operation (e.g., a convertor/locker/helper that doesn't consume the entire approved amount, a token with non-standard transfer semantics, or a prior call that reverted after tokens were already received) gets misattributed as newly-claimed reward belonging to whichever user calls `compound()` next.

### Finding Description
In `compound()`, for every registered reward token the contract computes: [1](#0-0) 
`receivedBalance = IERC20(_tokenAddress).balanceOf(address(this))` and then forwards/converts/locks that entire balance on behalf of `msg.sender`: [2](#0-1) 

This is identical in structure to the RevenueHandler flaw: distribution accounting is derived from the contract's total token balance rather than the amount actually received in this call (`multiclaimOnBehalf` return value or a before/after diff scoped to `msg.sender`'s claim). `multiclaimOnBehalf` is invoked once per `compound()` call: [3](#0-2) 

Similarly, the non-compoundable reward sweep uses the same pattern: [4](#0-3) 

If any registered reward token ever accumulates a residual balance in the `ManualCompound` contract that was not fully consumed/forwarded during a prior `compound()` invocation (for example, a `convertor`, `locker`, or `helper` that does not consume the full `receivedBalance` it was approved for, a fee-on-transfer/rebasing reward token, or a transaction that reverts downstream after `multiclaimOnBehalf` already moved tokens into the contract in a way that isn't atomically rolled back), that residual is silently swept into the next caller's `compound()` execution and paid out entirely to that caller. The original owner of the residual reward permanently loses it.

### Impact Explanation
Any unswept/residual reward balance belonging to one user is transferred to whichever unrelated wallet calls `compound()` next — a direct theft of unclaimed yield analogous to the RevenueHandler report, and a permanent loss for the original owner of the dust since there is no per-user internal ledger to reconcile the discrepancy. This satisfies the "theft or permanent freezing of unclaimed yield" impact class.

### Likelihood Explanation
`compound()` is a fully permissionless external entry point reachable by any ordinary wallet — no privileged role is required to trigger it. The likelihood of nonzero residuals depends on the exact behavior of the pluggable `_convertor`/`_locker`/`_helperAddress` contracts (e.g., `SmartWomConvert`, `VLMGP`, pool helpers) not consuming 100% of the approved `receivedBalance`, or on any registered reward token with transfer-amount deviations. Because `compoundableRewards`/`rewards` and their downstream converters/lockers are configurable via `addReward`/`setConvertor`/`setHelper`, exact severity depends on the specific tokens/converters wired in — this could not be fully confirmed from the indexed contract set alone (the downstream `convertFor`/`depositFor`/`lockFor` implementations were only partially inspected).

### Recommendation
Track the amount actually received from each `multiclaimOnBehalf` call (e.g., via before/after balance diff scoped strictly to the current transaction, or by using the return values already provided by `multiclaimOnBehalf`) instead of using the ambient `balanceOf(address(this))`, so that any pre-existing/residual balance is never attributed to the current caller.

### Proof of Concept
Conceptual scenario:
1. User A calls `compound()`; `multiclaimOnBehalf` credits reward token `X` to the `ManualCompound` contract. Downstream `_convertor.convertFor(receivedBalance, ...)` is approved for the full `receivedBalance` but, due to slippage/partial-fill logic in the convertor, only consumes 90% of it, leaving 10% as dust in the `ManualCompound` contract.
2. User B (unrelated) later calls `compound()` for their own unrelated rewards. The loop computes `receivedBalance = IERC20(X).balanceOf(address(this))`, which now includes both B's freshly claimed amount and A's leftover 10% dust. [5](#0-4) 
3. The entire `receivedBalance`, including A's residual, is converted/locked/transferred to B, permanently depriving A of their share.

### Citations

**File:** rewards/ManualCompound.sol (L123-125)
```text
    function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
        uint256 rewardTokensLength = rewards.length;        
        IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
```

**File:** rewards/ManualCompound.sol (L130-136)
```text
                for (uint j; j < rewardLength; j++) {
                    if (!compoundableRewards[_rewards[i][j]]) {
                        uint256 rewardBalance = IERC20(_rewards[i][j]).balanceOf(address(this));
                        if (rewardBalance > 0)
                            IERC20(_rewards[i][j]).safeTransfer(msg.sender, rewardBalance);
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
