### Title
Unprivileged `compound()` sweeps entire contract token balance instead of caller-specific amount, allowing theft of other users' unclaimed yield - ([File: rewards/ManualCompound.sol])

### Summary
`ManualCompound.compound()` is a fully public, unprivileged function (`external`, no `onlyOwner`/access-control modifier) that pulls rewards on behalf of `msg.sender` via `MasterMagpie.multiclaimOnBehalf`, but then disburses tokens based on `IERC20(token).balanceOf(address(this))` rather than the amount actually claimed for the calling user in that transaction. This mirrors the bug class in the reference report, where an unprivileged, unmodified public function let anyone move reward-token balances meant for other stakers to an arbitrary caller.

### Finding Description
In `rewards/ManualCompound.sol`, `compound()` [1](#0-0)  has two payout loops that both measure the amount to move using `IERC20(_tokenAddress).balanceOf(address(this))`:

- The "non-compoundable" loop sends the whole current balance of each token to `msg.sender`: [2](#0-1) 
- The "compoundable" loop iterates the registered `rewards` array and, for each token, reads `receivedBalance = IERC20(_tokenAddress).balanceOf(address(this))` and routes that entire balance (convert/lock/deposit/transfer) to `msg.sender`: [3](#0-2) 

Because the amount disbursed is the contract's *total* balance of the token — not an amount tied to the specific claim just performed for `msg.sender` — any tokens already sitting in the `ManualCompound` contract before the call (e.g. dust left over from a previous `compound()` call whose converter/locker/helper did not consume 100% of the balance, from rounding in `MasterMagpie`'s reward accounting, or from any other flow that leaves a residual balance in the contract) get swept in full to whichever address calls `compound()` next. Since `compound()` has no access control and can be called by any wallet with an empty or trivial `_lps`/`_rewards` argument, it is functionally similar to the reported `recoverErc20` bug: an unprivileged actor can extract token balances belonging to the protocol/other users rather than only their own newly-claimed rewards.

I was not able to fully verify from the indexed code whether `SmartWomConvert.convertFor` (the compoundable-path converter) always consumes 100% of the approved `receivedBalance` or can legitimately leave a remainder when `_convertRatio` is used, which would materially affect how easily a nonzero leftover balance accumulates in practice. This should be independently confirmed against `wombat/SmartWomConvert.sol`.

### Impact Explanation
If any accountable leftover balance can exist in `ManualCompound` between calls (dust, rounding, partial conversion, or a direct/incidental transfer to the contract), any wallet can call `compound()` with a minimal/empty claim and have that balance — which represents other users' unclaimed yield — routed entirely to itself. This is a theft of unclaimed yield belonging to other participants, satisfying the "theft or permanent freezing of unclaimed yield" impact bar.

### Likelihood Explanation
Likelihood depends on whether a nonzero balance can realistically accumulate in the contract between calls (e.g., through partial conversion, reward-claim rounding in `MasterMagpie`, or direct token transfers). The call itself requires no privilege and no cost beyond gas, so any wallet capable of noticing a nonzero `balanceOf` for a `rewards[]` token can immediately front-run and drain it via `compound()`.

### Recommendation
Track and disburse only the amount of tokens actually received/claimed for `msg.sender` during the current call (e.g., by measuring `balanceOf` before and after `multiclaimOnBehalf`, or by having `MasterMagpie` return per-token claimed amounts), instead of using the contract's total `balanceOf` as the payout amount in `compound()`.

### Proof of Concept
1. Assume `ManualCompound` accumulates a nonzero residual balance of a registered reward token `T` (e.g., from a prior `compound()` call where the converter/locker/helper for `T` did not consume the full `receivedBalance`, or from rounding dust left by `MasterMagpie.multiclaimOnBehalf`).
2. An attacker, who has no stake and no claimable rewards, calls `compound([], [], 0, 0, false)` (or with minimal/no-op `_lps`/`_rewards`).
3. `multiclaimOnBehalf` claims nothing new for the attacker, but the loop over `rewards[]` still reads `IERC20(T).balanceOf(address(this))` [4](#0-3)  and forwards the entire existing balance to the attacker via `safeTransfer`/`depositFor`/`lockFor`/`convertFor` depending on configuration [5](#0-4) .
4. The attacker receives token `T` that was never claimed on their behalf, at the expense of whichever user's yield produced that residual balance.

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
