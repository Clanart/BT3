## Title
Missing ERC20 return-value check in escrow withdrawal lets non-reverting tokens be marked settled without any transfer occurring - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron variant of `IntentGatewayV2.sol` reimplements token transfers using a raw low-level `.call()` to the ERC20 `transfer` selector and only checks that the *call itself* did not revert — it never checks the ERC20 `bool` return value. This is the exact bug class from the external report (missing check of `transfer`/`transferFrom` return value for non-compliant tokens that return `false` instead of reverting), but reproduced in Hyperbridge's own escrow settlement path rather than in a Frax-style AMM.

### Finding Description
In `withdraw()`, `onAccept()`'s `SweepDust` branch, and the tx-fee redemption block, tokens are moved via:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
``` [1](#0-0) 

This pattern checks only that the external call did not revert. Tokens that follow the "silent failure" ERC20 variant (return `false` on failure instead of reverting — e.g. BAT-style tokens, or any token with non-standard balance/blacklist/pausable logic) make the low-level `call` succeed (`success == true`) while the actual transfer never happens. Because the code treats a successful call as a successful transfer:

- `_orders[body.commitment][token] -= amount;` still executes, permanently marking the escrow entry as redeemed [2](#0-1) 
- `_filled[body.commitment] = beneficiary;` is set unconditionally at the top of `withdraw` before any transfer is attempted [3](#0-2) 
- The same unchecked pattern is used for `SweepDust` fund sweeps to a beneficiary in `onAccept` [4](#0-3) 
- And for transaction-fee redemption [5](#0-4) 

This is called from `onAccept` for `RedeemEscrow`/`RefundEscrow` request kinds, which is the settlement path reached after a cross-chain fill or cancellation is processed by the host [6](#0-5) .

By contrast, the mainline EVM `IntentGatewayV2.sol` / `IntrinsicIntents.sol` / `ExtrinsicIntents.sol` consistently use OpenZeppelin's `SafeERC20.safeTransferFrom`/`safeTransfer`, which reverts on a `false` return [7](#0-6) . The Tron fork diverges from this safe pattern and reintroduces the unchecked-transfer class of bug in the escrow settlement code path.

### Impact Explanation
If a pool/order is configured with an escrowed input token that returns `false` on failed transfer instead of reverting (deliberately or due to token-specific conditions such as a blacklist, pause, or insufficient contract balance from prior partial fills/rounding), `withdraw()` will:
1. Mark the commitment as filled/refunded (`_filled[commitment] = beneficiary`).
2. Decrement the internal escrow accounting (`_orders[commitment][token] -= amount`).
3. Emit `EscrowReleased`/`EscrowRefunded`.

...while never actually moving the tokens to the beneficiary. The escrowed tokens remain trapped in the contract, but the accounting and `_filled` state now falsely indicate settlement occurred. Since `_filled` is set unconditionally, there is no retry path — the legitimate beneficiary cannot re-claim the escrow through the normal flow, resulting in a permanent loss of the escrowed funds. This falls squarely under "stealing or loss of funds" and "false state acceptance" in the bounty's required impacts.

### Likelihood Explanation
Exploitability depends on whether a non-standard ERC20 token (returning `false` instead of reverting) is used as an escrowed asset — this is common for several real-world tokens (e.g., early ERC20 implementations, some stablecoin variants pre-audit fixes, tokens with blacklist/pause). Since `IntentGatewayV2` is a generic, permissionless intent-settlement gateway accepting arbitrary ERC20 tokens as inputs (no token allowlist enforced in `placeOrder`), any user placing an order with such a token — or any token that can be induced into a `false`-returning state — triggers the loss on settlement.

### Recommendation
- Replace all raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` patterns in `evm/tron/contracts/apps/IntentGatewayV2.sol` (`withdraw`, `onAccept`'s `SweepDust` branch, and fee redemption) with OpenZeppelin's `SafeERC20.safeTransfer`, matching the pattern already used in `IntrinsicIntents.sol`/`ExtrinsicIntents.sol`.
- Run Slither (or equivalent static analysis) across the Tron contracts specifically to catch other unchecked-transfer instances, and add this check to CI to prevent regression.

### Proof of Concept
1. Deploy the Tron `IntentGatewayV2` with a token `T` implementing `transfer` such that it returns `false` (without reverting) once a certain condition is met (e.g., recipient blacklisted, or balance insufficient due to a rounding edge case from fee-on-transfer/dust logic already present in `placeOrder`).
2. User places an order escrowing `T` as an input, source chain confirms escrow via `_orders[commitment][T] += amount`.
3. Order is filled on the destination chain; a `RedeemEscrow` post request is dispatched back and delivered via `onAccept` → `withdraw`.
4. Inside `withdraw`, `token.call(...)` to `T.transfer(beneficiary, amount)` returns `(true, encodedFalse)` — the low-level call succeeds but the ERC20 semantics indicate failure.
5. `withdraw` does not decode/check the returned boolean, so it proceeds to decrement `_orders[commitment][T]` and set `_filled[commitment] = beneficiary`, emitting `EscrowReleased`.
6. `beneficiary` never receives `T`; the tokens are stuck in the contract, and no legitimate retry is possible because `_filled` is already set — a permanent loss of the escrowed funds.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L683-684)
```text
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L708-714)
```text
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L17-30)
```text
import {IntentsBase} from "./IntentsBase.sol";
import {TokenInfo, Order, Params, WithdrawalRequest, FillOptions} from "@hyperbridge/core/apps/IntentGatewayV2.sol";
import {IDispatcher} from "@hyperbridge/core/interfaces/IDispatcher.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/**
 * @title IntrinsicIntents
 * @author Polytope Labs (hello@polytope.technology)
 *
 * @dev Same-chain intent logic: partial fills, same-chain cancel, and escrow release.
 */
abstract contract IntrinsicIntents is IntentsBase {
    using SafeERC20 for IERC20;
```
