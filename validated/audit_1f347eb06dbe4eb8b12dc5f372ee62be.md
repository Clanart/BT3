### Title
Balance-based sweep in `compound()` allows any caller to steal residual/dust reward tokens left in `ManualCompound` by a previous user - ([File: rewards/ManualCompound.sol])

### Summary
`ManualCompound.compound()` determines the amount to forward to `msg.sender` by reading `IERC20(token).balanceOf(address(this))` rather than tracking the delta actually received from `multiclaimOnBehalf` for that specific call. Both the "send back non-compoundable reward" loop and the main compounding loop use this balance snapshot, so any token balance sitting in the contract for any reason (a previous user's reverted/partial compound, a direct donation, or rounding dust) is swept entirely to whichever address next calls `compound()` with a `_lps`/`_rewards` array that happens to include that token address.

### Finding Description
In `rewards/ManualCompound.sol`:
```
function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
    ...
    IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
    for(uint256 i; i < _lps.length; i++) {
        ...
        if (!compoundableRewards[_rewards[i][j]]) {
            uint256 rewardBalance = IERC20(_rewards[i][j]).balanceOf(address(this));
            if (rewardBalance > 0)
                IERC20(_rewards[i][j]).safeTransfer(msg.sender, rewardBalance);
        }
        ...
    }
    for (uint256 i; i< rewardTokensLength; i++) {
        ...
        uint256 receivedBalance = IERC20(_tokenAddress).balanceOf(address(this));
        if (receivedBalance > 0) { ... transfer/convert/lock receivedBalance to msg.sender ... }
    }
``` [1](#0-0) 

Neither loop measures a pre/post balance delta around the `multiclaimOnBehalf` call — it simply reads whatever balance the contract currently holds for a given token and forwards the whole thing to `msg.sender`. `multiclaimOnBehalf` computes and transfers rewards accrued to `msg.sender`'s own `userInfo` in `MasterMagpie`, so an attacker referencing pools they never staked in gains nothing directly from that call (the claimed amount would be zero for those pools). However, the vulnerability is independent of that call's correctness: **any pre-existing balance of the reward token in the `ManualCompound` contract — regardless of source — is fully swept to the caller** as long as the caller supplies a `_lps`/`_rewards` combination that references that token address (even with zero actual claim amount, since `_rewards[i][j]` is caller-supplied and not validated against what was actually harvested).

This means:
1. If a prior `compound()` call by user A leaves behind unclaimed dust of a reward token in the contract (e.g., due to a convertor reverting after `safeApprove` but leaving unswapped balance, rounding leftovers from `IConverter`/`ISimpleHelper`, or an admin toggling `compoundableRewards` mid-flight causing tokens to go to the "compoundable" bucket instead of being swept back), that balance sits in the contract.
2. Any subsequent unprivileged caller can invoke `compound()` referencing that token in `_rewards`, and the entire existing balance (not just what they themselves are entitled to) is forwarded to them via the `balanceOf(address(this))` sweep pattern.

Existing checks do not stop this: there is no `nonReentrant` guard needed here (not a reentrancy issue), no per-call escrow, and no delta-based accounting on `rewardBalance`/`receivedBalance`.

### Impact Explanation
This is a custody/fund-safety issue: dust or leftover reward tokens belonging to (or ultimately owed back to) the protocol/other users can be misappropriated by an unrelated caller who never staked in or accrued those rewards. This matches "theft of unclaimed yield" — real economic loss of tokens that should have been returned to their rightful owner or swept back administratively.

### Likelihood Explanation
Requires a precondition where the contract's balance of a reward token is nonzero outside of an in-flight `compound()` transaction (e.g., a convertor/helper contract that doesn't consume 100% of `receivedBalance`, a revert mid-conversion leaving tokens stuck, or an admin toggling `compoundableRewards` between two transactions). Whether such residuals occur in practice depends on the specific `IConverter`/`ISimpleHelper`/`ILocker` implementations, which were not available for review — I could not confirm from the indexed files whether these external contracts always consume the full approved amount or can leave dust. This uncertainty affects the *feasibility/frequency* of a real-world residual balance, but the code pattern itself (balance-based sweep rather than delta-based) is a confirmed design flaw regardless of how often the precondition arises. No special privilege is needed by the attacker to exploit it once a residual exists — only a normal `compound()` call with matching token addresses in `_rewards`.

### Recommendation
Use delta-based accounting instead of raw `balanceOf(address(this))`: snapshot `balanceOf(address(this))` immediately before calling `multiclaimOnBehalf`, and only transfer/convert `(post - pre)` for each token, or restrict the swept amount to what `multiclaimOnBehalf` reports as claimed for `msg.sender`. Any leftover dust that predates the current transaction should be retained by the contract (recoverable only by owner via a dedicated admin sweep) rather than being available to any arbitrary caller.

### Proof of Concept
Foundry test outline:
1. Deploy `MasterMagpie`, `ManualCompound`, a mock reward token, and register the token via `addReward`.
2. Simulate a "residual" state: directly `transfer` reward tokens to the `ManualCompound` contract address (representing dust left from a prior interrupted compound or a reverting convertor), without any staking activity from the attacker.
3. As `attackerEOA` (who has zero stake/`userInfo` in the referenced `_lps` pools), call `compound(_lps, _rewards, 0, 0, false)` where `_rewards` includes the donated token's address.
4. Assert `multiclaimOnBehalf` transfers zero new tokens to `ManualCompound` for the attacker's pools (confirming no direct claim occurred).
5. Assert that despite this, `attackerEOA`'s balance of the reward token increases by the full donated/residual amount, and `ManualCompound`'s balance of that token drops to zero — proving the attacker captured tokens they never accrued.

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
