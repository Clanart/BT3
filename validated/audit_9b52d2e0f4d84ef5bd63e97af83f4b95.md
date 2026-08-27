### Title
Balance-based (not delta-based) reward distribution in `ManualCompound.compound()` allows theft of residual/leftover reward tokens belonging to other users - (File: rewards/ManualCompound.sol)

### Summary
`ManualCompound.compound()` sweeps the entire current `balanceOf(address(this))` of any reward token to `msg.sender`, both in the "non-compoundable reward" loop and in the main compounding loop, instead of tracking only the tokens received during the current call (delta accounting). Any token balance left in the contract from a prior call — whether dust, rounding remainder, or a token not fully consumed by a convertor/locker/helper — is fully claimable by the next caller.

### Finding Description
In `compound()`, after calling `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)`: [1](#0-0) 
the "send none compoundable reward back to caller" loop checks `!compoundableRewards[_rewards[i][j]]` and, if true, transfers `IERC20(_rewards[i][j]).balanceOf(address(this))` to `msg.sender` — the entire current balance, not the amount actually claimed in this transaction.

The same pattern repeats in the main compounding loop for registered reward tokens: [2](#0-1) 
`receivedBalance = IERC20(_tokenAddress).balanceOf(address(this))` is used directly as the amount forwarded to the convertor/locker/helper/`msg.sender`, again with no comparison against a pre-call snapshot.

Because the accounting is balance-based rather than delta-based, any token amount that remains in the contract after a previous `compound()` call — for example: a token whose helper/convertor/locker call doesn't fully consume the approved/transferred amount, a token registered as a reward that is later included by an attacker in `_rewards` while `compoundableRewards[token] == false` (e.g., after `removeReward` flips the flag, or a reward that was never added), or simple rounding dust from a prior claim — becomes fully available to the next arbitrary caller of `compound()`. `multiclaimOnBehalf` sends claimed rewards to `address(this)` (`ManualCompound`) rather than directly to the depositor, so anything not swept out atomically in the same transaction is unprotected principal sitting in the contract, since there is no reentrancy guard, per-user reward escrow, or snapshot/delta check preventing a subsequent unrelated caller from claiming it via `_rewards[i][j]` list manipulation.

An attacker does not need to cause the dust themselves; they only need to call `compound()` with `_lps`/`_rewards` arrays that name the residual token address while `compoundableRewards[token]` is false (or simply let it flow through the main loop if it's a registered reward token), triggering the balance-based transfer to themselves.

### Impact Explanation
This is theft of unclaimed/undistributed yield belonging to another user (Immunefi impact class: "theft of unclaimed yield" / "direct theft of user funds"). Any leftover balance from a legitimate user's earlier `compound()` call can be redirected to an attacker's own address on the very next call, with no way for the original depositor to recover it.

### Likelihood Explanation
Preconditions: some reward token balance must remain in `ManualCompound` after a legitimate `compound()` call (rounding dust, a helper/convertor not consuming 100% of the approved amount, or timing where multiple reward tokens are processed but only some get routed out). No special privileges, capital, or access are required by the attacker — any address can call `compound()` with an arbitrary `_lps`/`_rewards` array as long as `multiclaimOnBehalf` accepts it (attacker can supply empty/zero-value claim entries paired with `_rewards` entries naming the token to sweep). This is fully repeatable across every `compound()` call as long as dust exists, making it a standing risk rather than a one-off edge case.

### Recommendation
Replace balance-based accounting with delta accounting: snapshot `IERC20(token).balanceOf(address(this))` before calling `multiclaimOnBehalf`, and only transfer/process `balanceAfter - balanceBefore` for each token, for both the non-compoundable-reward-return loop and the main compounding loop. Alternatively, maintain a `mapping(address => uint256)` of tracked owed balances (e.g., via `multiclaimOnBehalf` return values or events) so that pre-existing residual balances can never be attributed to the current caller.

### Proof of Concept
Foundry test plan:
1. Deploy `ManualCompound` with a mock `MasterMagpie` whose `multiclaimOnBehalf` mints/transfers a configurable amount of a test ERC20 token directly to `ManualCompound` (simulating real claim behavior).
2. Register one reward token with a mock "helper"/"convertor" that intentionally does not consume 100% of the approved amount (or simply configure `compoundableRewards[X] = false` for a second token `X`).
3. User A calls `compound()` with `_rewards` causing `multiclaimOnBehalf` to send `100` of non-compoundable token `X` to `ManualCompound`. Simulate the "leftover" scenario: have the mock `multiclaimOnBehalf` deliberately leave `X` balance in the contract after A's call (e.g., by having a separate non-atomic step or simply not including `X` in A's `_rewards[i][j]` list so it isn't swept), asserting `IERC20(X).balanceOf(manualCompound) == 100` after A's tx.
4. User B (attacker), with no relation to A's claim, calls `compound()` with `_rewards` that lists token `X` (with `compoundableRewards[X] == false`) even though B's own `multiclaimOnBehalf` claim for `X` is `0`.
5. Assert `IERC20(X).balanceOf(B) == 100` and `IERC20(X).balanceOf(manualCompound) == 0` — i.e., B received A's leftover balance despite not having claimed it, and A has no path to recover the funds. This confirms the balance-based sweep transfers cross-user residual funds to an unrelated caller.

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
