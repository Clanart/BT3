# Title
`ManualCompound.compound` sweeps the contract's *entire* token balance to `msg.sender` regardless of what that caller actually claimed - ([File: rewards/ManualCompound.sol])

### Summary
`compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` distributes rewards based on `IERC20(_tokenAddress).balanceOf(address(this))` for every globally registered reward token, completely independent of the `_lps`/`_rewards` arguments actually used to claim on behalf of the caller. Any unprivileged address can call `compound` with arbitrary (even empty) `_lps`/`_rewards` and still trigger the second distribution loop, which will convert, lock, deposit, or transfer whatever balance currently sits in the contract to `msg.sender`.

### Finding Description
`compound` is structured in two independent stages:

1. `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)` pulls the caller's own accrued rewards for the supplied `_lps`/`_rewards` into the `ManualCompound` contract.
2. A second loop iterates the **state variable** `rewards` (all globally registered compoundable tokens) — not the caller-supplied `_rewards` — and for each one reads `receivedBalance = IERC20(_tokenAddress).balanceOf(address(this))` and disposes of the *entire* balance via convert/lock/helper/plain-transfer to `msg.sender`: [1](#0-0) 

Because this second loop is keyed off `rewards.length` and `balanceOf(address(this))` rather than the amount just claimed through `multiclaimOnBehalf` for *this* caller, the function has no way to distinguish "tokens this caller just earned" from "any token balance the contract happens to be holding." Concretely:

- In the locker branch, `ILocker(_locker).lockFor(receivedBalance, msg.sender)` locks the whole contract balance of `_tokenAddress` for `msg.sender`, even if `msg.sender` claimed nothing (e.g., calling with empty `_lps`/`_rewards`), so long as the token is registered with a `locker` set and `_lockMgp == true`. [2](#0-1) 
- Same issue applies to the convertor branch (`convertFor(receivedBalance, ...)`), helper branch (`depositFor(receivedBalance, ...)`), and the fallback `safeTransfer(msg.sender, receivedBalance)` used when no convertor/locker/helper is configured for a reward — exactly the precondition cited in the question. [3](#0-2) 
- The earlier "send non-compoundable reward back to caller" loop has the identical pattern: `IERC20(_rewards[i][j]).balanceOf(address(this))` is transferred in full to `msg.sender` for any token the caller lists in `_rewards`, again without tying the amount to what `multiclaimOnBehalf` actually delivered for that specific call. [4](#0-3) 

Nothing in `multiclaimOnBehalf`'s call or the surrounding code enforces that the balance read afterward is solely attributable to the current caller — there is no delta tracking (e.g., `balanceBefore`/`balanceAfter`), no `nonReentrant` guard, and no check binding `_tokenAddress` to the tokens actually specified in `_rewards`. Any residual balance already sitting in the contract — from rounding/remainder left over by a prior `convertFor`/`depositFor` call that doesn't consume 100% of the approved amount, from a stray/mistaken direct ERC20 transfer to the contract, or from any other source — gets attributed entirely to whichever address happens to call `compound` next, including with empty/irrelevant `_lps`/`_rewards` arrays that claim nothing.

### Impact Explanation
Any unprivileged caller can invoke `compound([], [], 0, 0, true)` (or with unrelated pools) and have the entire current balance of any registered reward token locked, converted, deposited, or transferred to themselves via `ILocker.lockFor`, `IConverter.convertFor`, `ISimpleHelper.depositFor`, or a plain `safeTransfer`. This is a direct theft vector for any reward tokens not perfectly and atomically consumed to zero by every prior call, since the accounting is balance-based rather than delta-based and is not scoped to the current caller's claim. This matches Critical - Direct theft of user funds, since locked/converted/transferred value is irreversibly attributed to an attacker who did not earn it.

### Likelihood Explanation
Exploitability does not require any special privilege — any EOA/contract can call `compound` at will and with arbitrary (including empty) `_lps`/`_rewards`. It does require that the contract currently hold a non-zero balance of a registered reward token not yet drained (e.g., leftover from imperfect consumption by an external `convertFor`/`depositFor` call, or a stray transfer). Given `ManualCompound` is meant to be used repeatedly by many stakers claiming through `masterMagpie`, and every legitimate call funnels tokens through this same contract before final disposal, any rounding remainder or delayed/partial external-call consumption creates an immediately and repeatably exploitable window for any observer to sweep the balance to themselves.

### Recommendation
Track the token balance delta specifically produced by this call's `multiclaimOnBehalf` invocation (e.g., `balanceBefore`/`balanceAfter` snapshots taken immediately before and after the claim call, per token), and only ever convert/lock/deposit/transfer that delta — never the raw `balanceOf(address(this))`. Additionally restrict the reward-distribution loop to only process tokens present in the caller-supplied `_rewards` (so a caller cannot trigger disposal of tokens unrelated to their own claim), and add reentrancy protection around the claim + distribute sequence.

### Proof of Concept
Hardhat/Foundry plan:
1. Deploy `ManualCompound` with a mock `masterMagpie`, mock reward token `R`, and mock `ILocker`.
2. Call `addReward(R, address(0), address(0), lockerMock)` (locker configured, no convertor/helper).
3. Simulate a residual balance: directly `R.mint(address(ManualCompound), 100e18)` (representing leftover dust from an earlier imperfect `lockFor`/`convertFor` consumption, or a stray transfer) — this is not the attacker's earned reward.
4. From an attacker address with zero legitimate stake/claims, call `compound([], [], 0, 0, true)`. `multiclaimOnBehalf` claims nothing for the attacker (mocked to no-op on empty arrays).
5. Assert `lockerMock.lockFor` is invoked with `amount == 100e18` and `receiver == attacker`, and `R.balanceOf(address(ManualCompound)) == 0` afterward — demonstrating the attacker locked/received tokens they never claimed via `multiclaimOnBehalf`, violating `IERC20(_rewards[i][j]).balanceOf(address(this)) == amount actually claimed by caller`.

### Citations

**File:** rewards/ManualCompound.sol (L127-138)
```text
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
