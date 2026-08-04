## Finding [1](#0-0) 

The Tron variant of `IntentGatewayV2` reproduces the exact bug class from the external report: it uses a raw low-level `.call()` to invoke `IERC20.transfer` and only checks that the *call itself* did not revert — it never decodes or validates the ERC-20 `bool` return value.

### Title
Escrow released/refunded without checking ERC20 `transfer` return value — (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`withdraw()` (called from `onAccept` on `RedeemEscrow`/`RefundEscrow`) and the `SweepDust` branch inside `onAccept` both move ERC-20 escrow funds using:

```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
``` [2](#0-1) [3](#0-2) 

`success` is only true/false based on whether the low-level call *reverted*. It says nothing about the ABI-encoded return data, which for ERC-20 `transfer` is a `bool` indicating whether the transfer actually succeeded. Tokens that return `false` instead of reverting on failure (a documented ERC-20 edge case, and the exact concern the external report raises about "implementations differ") will make `success == true` while no tokens actually moved.

### Finding Description
Contrast this with the sibling EVM contract `evm/src/apps/IntentGatewayV2.sol` / `IntentsBase.sol`, which correctly uses `SafeERC20.safeTransfer` for the same withdraw path:
```solidity
IERC20(token).safeTransfer(beneficiary, amount);
``` [4](#0-3) 

The Tron file even imports `SafeERC20` and uses `safeTransferFrom` for inbound escrow deposits [5](#0-4) , but the outbound `withdraw()` and `SweepDust` paths were written with a manual low-level call instead of `safeTransfer`, dropping the return-value check — the same class of bug flagged in the external `AssetVault.claimWithdrawalRequest` report.

Regardless of whether the transfer actually succeeds, the function unconditionally:
- decrements the escrow accounting: `_orders[body.commitment][token] -= amount;` [6](#0-5) 
- marks the order as filled: `_filled[body.commitment] = beneficiary;` [7](#0-6) 
- emits `EscrowReleased`/`EscrowRefunded` as if settlement succeeded [8](#0-7) 

### Impact Explanation
If the escrowed token returns `false` on transfer failure (rather than reverting) — e.g. due to a blacklist/frozen-recipient check, a paused state, or simply a non-reverting legacy ERC-20 implementation — the beneficiary receives nothing, yet the contract's internal state treats the withdrawal as fully settled and permanently marks the commitment as filled. Because `_filled[body.commitment]` is set unconditionally, there is no retry path: the escrowed tokens remain custodied in the contract balance but the accounting believes they were paid out, resulting in a permanent loss of the rightful beneficiary's funds with no recovery mechanism. This is a direct "stealing or loss of funds" / "false settlement acceptance" outcome reachable purely through the ISMP relayed message flow (`RedeemEscrow`/`RefundEscrow`/`SweepDust`), with no privileged actor, malicious relayer, or governance action required to trigger the loss condition — an ordinary user/solver simply needs to be paid in a token whose `transfer` can return `false`.

### Likelihood Explanation
Likelihood is medium: it requires the escrowed token to be one whose `transfer` implementation can return `false` on failure instead of reverting (uncommon among modern audited tokens, but not rare among legacy/deployed tokens, and Tron's TRC-20 ecosystem in particular has several tokens with non-standard/legacy transfer semantics — directly relevant since this is the Tron deployment of the contract). No attacker action is even required — this is a silent fund-loss bug that triggers under normal operation whenever such a token is used and any transfer-failure condition arises (e.g., recipient blacklisted, contract paused, insufficient allowance edge cases in the SweepDust path).

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` and the `SweepDust` handling within `onAccept` with `SafeERC20.safeTransfer`, consistent with the rest of the codebase (`evm/src/apps/intentsv2/IntentsBase.sol`, `WrappedHyperFungibleToken.sol`, etc.), so that both call failure and a `false` boolean return revert the transaction before escrow state is mutated or the order is marked filled.

### Proof of Concept
1. Deploy (or use) an ERC-20 token whose `transfer` returns `false` instead of reverting when, e.g., the recipient is blacklisted or the contract is paused (a legitimate, standards-permitted ERC-20 behavior).
2. Create and fill an intent order on the Tron `IntentGatewayV2` escrowing that token.
3. Trigger the ISMP `RedeemEscrow` (or `RefundEscrow`) flow so `onAccept` calls `withdraw(body, ...)`.
4. Have the token's `transfer` return `false` for the beneficiary address at settlement time (e.g., beneficiary gets blacklisted between escrow and withdrawal, or the token is paused).
5. Observe: `token.call(...)` does not revert (`success == true`), so `withdraw()` proceeds to decrement `_orders[commitment][token]`, set `_filled[commitment] = beneficiary`, and emit `EscrowReleased`/`EscrowRefunded` — even though the beneficiary's balance never increased. The escrowed tokens are now stuck in the contract with no path to reclaim them, since the commitment is already marked filled.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L398-399)
```text
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-667)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-721)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

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

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }

        if (isRefund) {
            emit EscrowRefunded({commitment: body.commitment});
        } else {
            emit EscrowReleased({commitment: body.commitment});
        }
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-409)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
