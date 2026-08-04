### Title
Unchecked ERC20 return-value in `withdraw()`/`SweepDust` treats failed (no-revert) transfers as successful, permanently burning escrowed funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron variant of `IntentGatewayV2` pays out escrowed order tokens using a raw low-level `.call()` and only checks that the *call itself* did not revert, never decoding the ERC20 `bool` return value. This is the exact root cause of the referenced Teller Finance report: tokens that signal transfer failure by returning `false` instead of reverting (the "ZRX/no-revert-on-failure" class) will make `success == true` even though no value moved, so the contract's internal escrow accounting is decremented and the order is marked filled/refunded as if the beneficiary was paid.

### Finding Description
In `withdraw()`, the internal function invoked from `onAccept` for `RedeemEscrow`/`RefundEscrow`, and again in the `SweepDust` handler, token payouts are done like this: [1](#0-0) [2](#0-1) [3](#0-2) 

`token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` returns `success = true` as long as the target contract executes without reverting — it says nothing about the ABI-decoded boolean the ERC20 standard uses to signal transfer failure. Any token that follows the pre-EIP20-fix pattern (returns `false` on failure rather than reverting, e.g. ZRX/BNB-style tokens explicitly called out in the seed report) will pass this check even when the transfer did not occur.

Immediately after this unchecked "success" check, the code unconditionally mutates escrow state and marks the order settled:
- `_orders[body.commitment][token] -= amount;` is decremented regardless of whether tokens actually reached `beneficiary`.
- `_filled[body.commitment] = beneficiary;` is set before the loop even runs, and `EscrowReleased`/`EscrowRefunded` fires unconditionally once the loop completes.

This is structurally identical to the seed bug: `LenderCommitmentGroup_Smart.addPrincipalToCommitmentGroup()` used unchecked `transferFrom()` and then mutated protocol state (`totalPrincipalTokensCommitted`, minted shares) as if the transfer succeeded. Here, `withdraw()` uses unchecked `.call()`-wrapped `transfer()` and then mutates escrow accounting (`_orders`) and settlement state (`_filled`, `EscrowReleased`) as if the payout succeeded.

By contrast, the escrow *deposit* paths in the same contract and in the mainline EVM `IntentGatewayV2.sol` correctly use `SafeERC20.safeTransferFrom`, which reverts on a `false` return: [4](#0-3) 
This shows deposits are hardened against this exact class, but the withdrawal/redemption path in the Tron contract was not, breaking the intended invariant that escrowed funds move exactly once and only to the rightful beneficiary.

### Impact Explanation
Since order `token` addresses are attacker-supplied fields decoded straight from `order.inputs`/`order.outputs` (`address(uint160(uint256(...)))`) with no allowlist, a user can construct/fill an order whose token address is a no-revert-on-failure ERC20. When `RedeemEscrow`/`RefundEscrow` executes `withdraw()`, that token's slot in `_orders[commitment][token]` is silently zeroed out and the order is marked `EscrowReleased`/`EscrowRefunded` even though the beneficiary received nothing for that leg — while other legitimate legs in the same withdrawal batch (native ETH, correctly-behaving tokens, protocol fees) still pay out. This corrupts the "moves exactly once to the rightful beneficiary" invariant for bridge-custodied assets: funds are locked/lost in the contract with no remaining accounting entry to reclaim them (the escrow slot is already decremented to zero and the commitment is already marked filled/refunded), and the on-chain event log falsely reports a successful release.

### Likelihood Explanation
No privileged access is required: any user creating or filling an order chooses the token addresses, and this path is reached through the normal, unprivileged `onAccept` → `withdraw()` flow triggered by legitimate ISMP redemption/refund messages. The only precondition is use of a no-revert-on-failure ERC20 as an order token — exactly the token class the underlying seed report demonstrates is realistic (ZRX, and similar legacy tokens still in circulation on Tron/BSC-style chains this contract variant targets).

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` and the `SweepDust` branch of `onAccept` with OpenZeppelin's `SafeERC20.safeTransfer`, which already decodes and validates the return value and is already imported/used elsewhere in this same contract (`using SafeERC20 for IERC20;`). This guarantees a `false` return reverts the transaction instead of allowing escrow state to advance past a payout that never happened.

### Proof of Concept
1. Deploy a `NoRevertToken` (as shown in the seed report) as the `token` for one leg of an order's `inputs`/`outputs`.
2. Create and fill a cross-chain order using this token; the deposit-side `safeTransferFrom` succeeds normally (attacker funds it correctly to avoid reverting at deposit time).
3. Drain/manipulate the token's own balance mapping for the `IntentGatewayV2` contract (e.g., via a second call the attacker controls if the token is attacker-deployed) so that at redemption time `balanceOf(gateway) < amount`.
4. Trigger `RedeemEscrow` via the normal ISMP `onAccept` flow. Inside `withdraw()`, `token.call(...transfer...)` returns `success = true` (the call executes without reverting) even though the token's internal `transfer` logic returns `false` and moves zero balance.
5. `_orders[commitment][token] -= amount` still executes, `EscrowReleased` fires, and `_filled[commitment]` is set — the beneficiary receives nothing for that token while the contract's records show a completed release, and there is no remaining path to reclaim the un-transferred amount.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L452-454)
```text
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-671)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
                unchecked {
                    ++i;
                }
                emit DustSwept(token, amount, req.beneficiary);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L693-705)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L707-714)
```text
        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }
```
