### Title
Unscoped reward sweep in `compound` lets any caller drain the contract's full balance of every configured reward token - (File: rewards/ManualCompound.sol)

### Summary
`compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` claims rewards on behalf of `msg.sender` via `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)`, but the settlement loop that actually pays out tokens iterates the entire configured `rewards` storage array and blindly transfers `IERC20(_tokenAddress).balanceOf(address(this))` to `msg.sender` for every reward token, independent of what `_lps`/`_rewards` the caller passed. Any leftover balance of a configured reward token sitting in the contract — from dust, rounding, or another user's in-flight claim — is swept entirely to whichever address calls `compound` next.

### Finding Description
In `rewards/ManualCompound.sol`: [1](#0-0) 
the first loop only handles non-compoundable tokens explicitly named in the caller-supplied `_rewards[i]` arrays, transferring back to the caller. The second, separate loop: [2](#0-1) 
ignores the caller's `_lps`/`_rewards` input entirely and instead loops over `rewards.length` (the global configured reward list), computing `receivedBalance = IERC20(_tokenAddress).balanceOf(address(this))` and dispatching the **entire contract balance** of that token to `msg.sender` (via convert, lock, deposit, or direct transfer).

Because settlement is keyed off the contract's total balance rather than the amount actually produced by the caller's own claim, any reward tokens already resident in the contract before the call (e.g., from a prior transaction where the recipient contract/locker/convertor reverted after transferring in, from rounding remainders left after a previous `compound` call, or from tokens sent to the contract by any means) get attributed entirely to the current caller. A caller can pass `_lps`/`_rewards` producing a claim of near-zero value (or even an empty/no-op claim path) and still receive the full swept balance of every configured reward token held by the contract at call time.

### Impact Explanation
If the contract accumulates any balance of a configured reward token between calls (dust from `safeApprove`/exact-amount conversions, timing gaps between claim and settlement, or partial failures in downstream `convertFor`/`lockFor`/`depositFor` calls that don't consume 100% of the approved amount), the next caller of `compound` receives that entire balance regardless of their own claim size. This is a direct misappropriation of funds that rightfully belong to other users' unclaimed/settled rewards, matching a fund-theft impact class, though the magnitude is bounded by however much residual balance can realistically accumulate in the contract (this depends on downstream converter/locker/helper behavior which is out of scope of this file).

### Likelihood Explanation
Exploitability requires the contract to actually be holding a non-zero balance of a configured reward token at the time of the attacker's call — this is a precondition outside the attacker's direct control unless they can also induce or race a deposit (e.g., front-run another user's `compound` call, or exploit converter/locker functions that leave dust). Given `safeApprove`+full-balance patterns, exact-amount downstream calls (`convertFor`, `lockFor`, `depositFor`) are given the full `receivedBalance`, so under normal operation with atomic single-block execution, residual dust may be minimal per call, but it is cumulative across all callers and pools over time, and any single call that leaves so much as unswept dust is exploitable by the very next caller with no special capital requirement — any EOA can call `compound` with trivial or fabricated `_lps`/`_rewards` inputs.

### Recommendation
Track reward-token balances as deltas scoped to the specific claim performed in the current call (e.g., snapshot `balanceOf(address(this))` before calling `multiclaimOnBehalf` and only settle the increase), and/or restrict the settlement loop to only the tokens actually present in the caller-supplied `_rewards` arrays rather than iterating the full global `rewards` list and paying out the total contract balance.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `ManualCompound` with a mock `masterMagpie`, a configured reward token `R` with `tokenHelper`/`convertor`/`locker` all zero (so it falls into the direct `safeTransfer` branch).
2. Simulate residual balance: have the mock `masterMagpie.multiclaimOnBehalf` leave `R` balance of `100` in the `ManualCompound` contract after a first legitimate user's `compound` call (e.g., by having the converter path leave dust, or by directly minting `R` tokens to the `ManualCompound` contract to simulate accumulated dust/leftover from a partial claim).
3. From a second, unrelated attacker EOA with no stake and no legitimate claim, call `compound(_lps=[], _rewards=[], _convertRatio=0, _minRec=0, _lockMgp=false)`.
4. Assert: the second loop iterates `rewards.length` regardless of `_lps`/`_rewards` being empty, computes `IERC20(R).balanceOf(address(this)) == 100`, and transfers the full `100` to the attacker via `IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance)` at [3](#0-2) , even though the attacker's claim (`_lps=[]`) produced zero reward tokens.
5. Assert the attacker's `R` balance increased by `100` while their actual claimed share was `0`, proving `balanceOf(address(this))` diverges from the caller's own claimed share.

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
