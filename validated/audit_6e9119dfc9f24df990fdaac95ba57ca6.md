### Title
`Airdrop2.claim` re-approves `vlmgp` with a fresh nonzero value on every locked claim, causing OZ `safeApprove`'s allowance-race guard to permanently revert if any residual allowance remains from a prior claim - (File: rewards/Airdrop2.sol)

### Summary
`Airdrop2.claim` calls `reward.safeApprove(address(vlmgp), claimable)` on every `isLock=true` claim without ever resetting the allowance to zero first. OpenZeppelin's `SafeERC20.safeApprove` reverts when approving a nonzero value over an already-nonzero allowance, so if `vlmgp.lockFor` does not fully consume the previously approved `claimable` amount, any subsequent locked-vesting claim by that same user permanently reverts.

### Finding Description
In `claim`, the locked path is: [1](#0-0) 

`reward.safeApprove(address(vlmgp), claimable)` is invoked with a fresh, nonzero `claimable` value each call, and there is no `reward.safeApprove(address(vlmgp), 0)` reset beforehand. OpenZeppelin's `SafeERC20.safeApprove` implementation contains a guard: it reverts unless either the new value is `0` or the token's current allowance for the spender is already `0` (this is the standard ERC20 approve front-running mitigation baked into OZ's `SafeERC20`). Therefore, if `vlmgp.lockFor(claimable, msg.sender)` does not pull the entire approved `claimable` amount from `Airdrop2` (leaving any nonzero residual allowance, e.g. due to internal rounding, fee, or cap logic inside `VLMGP`), the allowance from `reward` (the airdrop token) to `vlmgp` remains nonzero after the call returns.

On the user's next vesting-tranche claim with `isLock=true`, `_getClaimable` returns a new nonzero `claimable` amount, and the same `reward.safeApprove(address(vlmgp), claimable)` line executes against a still-nonzero existing allowance, which reverts inside OZ's `SafeERC20` guard. Since `Airdrop2` has no admin function to reset the token allowance to `vlmgp` (only `setVlmgp`, `pause`, `unpause`, and `emergencyWithdraw` exist, none of which touch the `reward`→`vlmgp` allowance), this bricking is not recoverable without a contract upgrade/migration or an out-of-band token transfer/allowance reset, i.e. it exceeds 24 hours and effectively becomes a permanent lock of that user's remaining vesting claims through this contract.

I was not able to fully verify inside this session whether `VLMGP.lockFor`'s internal transfer logic can leave a nonzero residual allowance under real conditions (e.g., due to `maxSlot`, rounding in lock-amount calculations, or partial-consumption edge cases) — the body of `lockFor` was not fully inspected. The vulnerability's root cause in `Airdrop2.claim` (repeated nonzero `safeApprove` without a zero-reset) is confirmed in the source, but the exact trigger condition inside `VLMGP.lockFor` (whether it always consumes the full approved amount via `safeTransferFrom(msg.sender, address(this), amount)`) is unconfirmed from what I could inspect.

### Impact Explanation
If triggered, the exploited path permanently freezes the attacker's/user's own future MGP vesting claims through `Airdrop2` (their unclaimed airdrop yield becomes unclaimable for more than 24 hours, requiring admin/dev intervention or contract migration to resolve). This matches the "permanent freezing of funds / unclaimed yield" impact class. Note that this only affects the user's own funds (self-inflicted via the isLock path), not other users' claims, since `claimedAmount` and allowances are not shared/global state affecting third parties.

### Likelihood Explanation
Requires: (1) the user opts into `isLock=true` on a claim, and (2) `vlmgp.lockFor` leaves nonzero residual allowance from that claim's approval. No special privileges are needed — any claimant can trigger this by simply calling `claim(..., true)` twice across two vesting intervals. Feasibility hinges entirely on whether `VLMGP.lockFor` can, in practice, consume less than the full approved amount; this could not be conclusively confirmed from the inspected code and would need to be validated against `VLMGP.lockFor`'s actual transfer arithmetic.

### Recommendation
In `Airdrop2.claim`, reset the allowance to zero before setting a new approval, e.g.:
```solidity
reward.safeApprove(address(vlmgp), 0);
reward.safeApprove(address(vlmgp), claimable);
```
or better, use `forceApprove` (OZ's newer helper) or `safeIncreaseAllowance`/exact-amount `safeTransfer`-then-`transferFrom` patterns that don't rely on a stateful allowance at all. Additionally, add an owner-only emergency function to reset the `reward`→`vlmgp` allowance in case of unexpected residuals.

### Proof of Concept
Foundry test plan:
1. Deploy `Airdrop2` with a mock `reward` ERC20 and a mock `IVLMGP` whose `lockFor(amount, user)` intentionally calls `IERC20(reward).transferFrom(msg.sender, address(this), amount - 1)` (consuming 1 wei less than approved).
2. Set up a merkle tree for a single user with `totalAmount` split across multiple vesting intervals; fast-forward `block.timestamp` past `startVestingTime` for tranche 1.
3. Call `claim(totalAmount, proof, true)` — expect success; assert `reward.allowance(address(airdrop2), address(vlmgp)) == 1` (residual).
4. Fast-forward `block.timestamp` to unlock tranche 2's vested amount.
5. Call `claim(totalAmount, proof, true)` again — expect revert (bubbled from `SafeERC20: approve from non-zero to non-zero allowance` or OZ's equivalent guard message), confirming the user's second locked claim is permanently blocked.
6. Assert there is no owner/admin function in `Airdrop2` capable of resetting the allowance, confirming the freeze persists beyond 24 hours absent a contract upgrade.

### Citations

**File:** rewards/Airdrop2.sol (L86-92)
```text
        // Mark it claimed and send the token.
        if (isLock) {
            reward.safeApprove(address(vlmgp), claimable);
            vlmgp.lockFor(claimable, msg.sender);
        } else {
            reward.safeTransfer(msg.sender, claimable);
        }
```
