### Title
Stranded reward dust in ManualCompound.compound is swept by the next unrelated caller - (File: rewards/ManualCompound.sol)

### Summary
`compound()` computes the amount to convert/lock/forward for each tracked reward token as `IERC20(_tokenAddress).balanceOf(address(this))` [1](#0-0) , i.e. the entire contract balance rather than an amount attributable to `msg.sender`'s own claim. Any residual balance left on the contract from a prior transaction (e.g. rounding in `SmartWomConvert._convertFor`, a `_convertRatio`/`_minRec` combination that leaves leftover wom/mWom, or any helper/locker that does not consume 100% of the approved amount) is fully swept and credited to whichever address happens to call `compound` next.

### Finding Description
`compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` first calls `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)` [2](#0-1) , which pulls `msg.sender`'s claimable rewards into this contract. It then loops over the caller-supplied `_lps`/`_rewards` to return any non-compoundable token balances to the caller [3](#0-2) . Finally, for every globally tracked `rewards[i]` entry (independent of what the caller actually claimed), it reads `receivedBalance = IERC20(_tokenAddress).balanceOf(address(this))` and converts/locks/forwards/transfers that *entire* balance to `msg.sender` [4](#0-3) .

There is no per-user accounting of how much of a compoundable token balance actually belongs to the current caller versus what was left over from a previous `compound()` call. If a prior call's `IConverter.convertFor` (e.g. `SmartWomConvert._convertFor`) leaves dust — due to integer division in `buybackAmount`/`convertAmount` splitting [5](#0-4) , router slippage, or any helper/locker not consuming the full approved amount — that dust remains as an ERC20 balance on `ManualCompound`. The very next caller to invoke `compound` (even with empty `_lps`/`_rewards` arrays, which skips `multiclaimOnBehalf` claiming anything new and skips the "return non-compoundable" loop entirely) will still have that stranded balance swept in the second loop and forwarded/converted/locked to themselves, because the code cannot distinguish "balance I am owed from my own claim" from "balance still sitting here from someone else's earlier, imperfectly-consumed compound."

The `compoundableRewards` mapping only tracks which token addresses are compoundable (a boolean membership check used in the first loop), not accounting of ownership of the token balance — so the invariant "residual value must be attributed to its owner" is not, and cannot be, enforced by this mapping. This confirms the described root cause: the contract has no reconciliation between `compoundableRewards[token]` bookkeeping and actual attributable claim amounts; it just blindly sweeps `balanceOf(address(this))`.

### Impact Explanation
Any dust left behind by an imperfect conversion, rounding, or partial consumption by a helper/locker/converter becomes claimable in full by the next arbitrary caller of `compound`, including an attacker who calls with empty `_rewards` arrays purely to trigger the sweep-and-forward loop for tokens they never claimed. This is a direct, quantifiable transfer of value that rightfully belongs to a previous user (or the protocol) to an unrelated caller — theft of unclaimed/residual yield.

### Likelihood Explanation
The precondition is simply that some non-zero dust balance exists on the `ManualCompound` contract at the time of a call — which is highly likely in normal operation given integer-division splits in `_convertFor`, potential router slippage, and any helper that does not consume exactly the approved amount. No special privileges, capital, or complex setup are required: any EOA can call `compound` with empty inner arrays to trigger the sweep of whatever balance currently sits on the contract for every tracked reward token, and this is repeatable every time dust accumulates.

### Recommendation
Track owed balances per compoundable token before and after `multiclaimOnBehalf`, and only convert/lock/forward the delta actually attributable to `msg.sender`'s claim in this call (e.g. `balanceBefore`/`balanceAfter` snapshot around `multiclaimOnBehalf`), rather than using the raw `balanceOf(address(this))`. Any genuine leftover dust should be swept to a protocol-controlled address or accumulated in a per-token "unallocated" bucket rather than being fully assignable to whichever address calls next.

### Proof of Concept
Hardhat test plan:
1. Deploy `ManualCompound` with a mocked `masterMagpie`, a mocked `IConverter` (`SmartWomConvert`-like) that intentionally leaves 1 wei of `mWom` unconverted/unswept on itself after `convertFor`, and register this token via `addReward`.
2. As User A, call `compound` with `_lps`/`_rewards` that trigger `multiclaimOnBehalf` to deposit some `tokenAddress` balance into `ManualCompound`; let the mocked converter consume all but 1 wei, leaving 1 wei of `tokenAddress`/output token stranded on `ManualCompound`.
3. As User B (unrelated EOA, no prior claim), call `compound(_lps=[], _rewards=[[]], ...)` (empty arrays so `multiclaimOnBehalf` claims nothing new for B).
4. Assert that despite claiming/contributing 0 to the reward token balance, User B's `compound` call still triggers the token loop's `receivedBalance > 0` branch and forwards/converts/locks the leftover 1 wei to User B — i.e., `IERC20(tokenAddress).balanceOf(manualCompound)` goes from 1 to 0 and User B receives value they never claimed.
5. This demonstrates that `compoundableRewards[token]` bookkeeping (a boolean) does not reconcile with actual attributable balances in `rewards[i].tokenAddress`, and that residual value is claimable by an arbitrary next caller rather than being attributed to its rightful owner.

### Citations

**File:** rewards/ManualCompound.sol (L123-125)
```text
    function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
        uint256 rewardTokensLength = rewards.length;        
        IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
```

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

**File:** wombat/SmartWomConvert.sol (L182-183)
```text
        uint256 buybackAmount = _amount - (_amount * _convertRatio / DENOMINATOR);
        uint256 convertAmount = _amount - buybackAmount;
```
