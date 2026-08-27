Confirmed: `WombatStaking.sol` has no `address(this).balance`-based rescue, sweep, or emergency withdraw function for native BNB anywhere in the contract. The only place native BNB balance is ever moved out is inside `vote()` via `Address.sendValue(payable(wbnb), rewardAmount)`, using the accounted `rewardAmount` return value from `voter.vote()`, not the actual contract balance.

### Title
Native BNB sent directly to WombatStaking is permanently locked with no recovery path - (File: wombat/WombatStaking.sol)

### Summary
`WombatStaking.sol` exposes an unrestricted `receive() external payable {}` function [1](#0-0)  that allows any wallet to send native BNB to the contract. However, the contract has no function that uses `address(this).balance` to move BNB out — the only egress path for native BNB is inside the owner/bribeManager-restricted `vote()` function, which forwards BNB using the `rewardAmount` value returned from an external `voter.vote()` call, not the actual balance held by the contract.

### Finding Description
The `receive()` fallback is public and payable with no access control [1](#0-0) , so any unprivileged wallet can transfer BNB directly to `WombatStaking`. This is analogous to the Compound Timelock/H01 bug class: the contract accepts native value but never accounts for or spends it based on its real balance.

The only code path that ever moves BNB out of the contract is inside `vote()`: [2](#0-1) 
Here `rewardAmount` originates strictly from the array returned by `voter.vote(_lpVote, _deltas)` [3](#0-2) , wrapping only that reported amount into WBNB via `Address.sendValue(payable(wbnb), rewardAmount)`. There is no logic anywhere in the contract that reads `address(this).balance` and forwards/rescues it, and no `emergencyWithdraw`/`sweep`/owner rescue function exists for native BNB (confirmed absent by search across the file). Once BNB lands in the contract via a direct transfer (not as part of a `vote()`-reported bribe reward), it can never be referenced or moved again by any function in the contract — it is permanently orphaned.

### Impact Explanation
Any BNB sent directly to the `WombatStaking` contract address (whether by user error, or excess/mismatched native bribe rewards paid by the external Wombat voter/bribe contracts beyond what `voter.vote()` reports) becomes permanently unrecoverable. This is a permanent freezing of funds with no admin or user-facing recovery mechanism, satisfying the "permanent freezing of funds" impact bar.

### Likelihood Explanation
Likelihood is moderate: this requires either (a) a wallet/integrator mistakenly sending BNB directly to the `WombatStaking` address (a common user error pattern, especially since the contract is a well-known, on-chain address used by pool helpers such as `AnkrBNBPoolHelper`/`WombatPoolHelper`), or (b) a discrepancy between actual native BNB received from the Wombat voter/bribe system and the `rewardAmount` values it reports, leaving unaccounted dust permanently stuck. No privileged role or malicious admin action is required to trigger the loss — only a plain native-token transfer transaction from any wallet.

### Recommendation
Follow the same remediation path referenced in the original report (the OpenZeppelin approach): base the BNB-forwarding logic in `vote()` (or add a dedicated function) on the actual `address(this).balance` (or the balance delta before/after the external call) rather than solely trusting the externally-reported `rewardAmount`. Additionally, consider removing the unrestricted `receive()` or adding an owner-gated sweep function that forwards any stray native balance (e.g., wrap the full `address(this).balance` into WBNB and queue it as rewards) so no BNB can become permanently unreachable.

### Proof of Concept
1. Any wallet calls `WombatStaking.call{value: 1 ether}("")` (a plain transfer), which succeeds due to the unrestricted `receive()` [1](#0-0) .
2. The contract's BNB balance increases by 1 ether, but no state variable tracks this.
3. `bribeManager` later calls `vote()`; the only BNB ever forwarded out is `rewardAmount` as returned by `voter.vote()` [4](#0-3) , which is unrelated to the stray 1 ether sitting in the contract.
4. No function in the contract (verified by full-file review and search) ever reads `address(this).balance` for BNB or exposes a rescue/sweep function.
5. The 1 ether sent in step 1 remains locked in the `WombatStaking` contract permanently, with no code path capable of retrieving it.

### Citations

**File:** wombat/WombatStaking.sol (L201-202)
```text
    /// @notice payable function needed to receive BNB
    receive() external payable {}
```

**File:** wombat/WombatStaking.sol (L373-374)
```text
            revert LengthMismatch();
        uint256[][] memory rewardAmounts = voter.vote(_lpVote, _deltas);
```

**File:** wombat/WombatStaking.sol (L386-395)
```text
                for (uint256 j; j < rewardAmounts[i].length; j++) {
                    uint256 rewardAmount = rewardAmounts[i][j];
                    uint256 callerFeeAmount = 0;

                    if (rewardAmount > 0) {
                        // if reward token is bnb, wrap it first
                        if (address(rewardTokens[i][j]) == address(0)) {
                            Address.sendValue(payable(wbnb), rewardAmount);
                            rewardTokens[i][j] = IERC20(wbnb);
                        }
```
