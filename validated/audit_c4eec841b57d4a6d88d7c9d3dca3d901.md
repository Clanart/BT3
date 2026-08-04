I have sufficient evidence to build the finding. The key point: `withdraw()` (reachable from `cancelOrder` for same-chain orders, and from `onAccept`/`onGetResponse` for cross-chain flows) sends escrowed tokens out using a raw low-level `.call` with `IERC20.transfer.selector`, checking only that the *call itself* did not revert (`success`) — never inspecting the boolean return value that ERC20 mandates for a real success/failure signal. This is the exact "no SafeERC20" pattern from the external report, just on the outbound leg instead of inbound, and it exists in the same file that already imports and uses `SafeERC20`/`safeTransferFrom` for inbound transfers, making the omission an inconsistency rather than a deliberate design choice.

### Title
Unchecked ERC20 `transfer` return value in escrow `withdraw()` lets non-reverting tokens fake payout while burning escrow accounting - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.withdraw()` releases escrowed order tokens with `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` and only checks that the low-level `call` succeeded, never decoding/validating the ERC20 boolean return value [1](#0-0) . The same unchecked pattern is used for transaction fee payout and for `SweepDust` handling in `onAccept` [2](#0-1) [3](#0-2) . This mirrors the reported bug class exactly (no `SafeERC20`, code assumes reverts-on-failure) even though the contract already imports and uses `SafeERC20`/`safeTransferFrom` for every inbound token pull [4](#0-3) [5](#0-4) [6](#0-5) .

### Finding Description
`withdraw()` is the single internal function that finalizes escrow release for both `RedeemEscrow` and `RefundEscrow` request kinds, reachable via:
- `onAccept()` on any authenticated cross-chain `RedeemEscrow`/`RefundEscrow` post request [7](#0-6) 
- `cancelOrder()` for same-chain cancellation, called directly by the order owner without any cross-chain proof [8](#0-7) 
- `onGetResponse()` for cross-chain cancellation after a storage-proof GET response [9](#0-8) 

Inside `withdraw()`, for every non-native token, the escrow ledger `_orders[commitment][token]` is decremented by `amount` and the code treats the transfer as done purely based on whether the low-level `call` itself did not revert:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
_orders[body.commitment][token] -= amount;
```
Standard ERC20 (per EIP-20) allows `transfer` to return `false` on failure instead of reverting. Several real, non-malicious tokens historically implement exactly this pattern (the same class cited in the source report: BAT, ZRX-era tokens, etc.). If the escrowed token used in an order behaves this way and the transfer fails to move value (e.g., due to a paused state, blacklist, or any other internal condition returning `false`), `success` is still `true` (the raw call did not revert), so the code proceeds to mark the order finalized (`_filled[commitment] = beneficiary`) and decrements the escrow accounting as if funds were paid out — while the beneficiary receives nothing. The fee payout at line 711 has the identical flaw for the protocol fee token.

This is a direct analog of the reported `LockManagerERC20.lock()` bug: the contract assumes a call's non-revert implies a true ERC20 success, which is false for tokens that signal failure via return value rather than revert. The difference is direction — the report's bug inflates a locked balance on inbound transfer; here it finalizes/burns escrow accounting on an outbound transfer that silently failed to deliver funds, permanently orphaning the beneficiary's entitled tokens inside the contract while the commitment is marked as settled, preventing any retry (since `_filled`/`_orders` are already updated and re-processing the same commitment reverts as `Filled`/`UnknownOrder`).

### Impact Explanation
Once `withdraw()` marks a commitment filled/refunded and decrements `_orders`, there is no compensating path — `RedeemEscrow`/`RefundEscrow` cannot be replayed for the same commitment (`Filled()` on repeat cancel, `UnknownOrder()` on repeat withdraw since the ledger is already zeroed). The legitimate filler or user permanently loses their entitled escrowed tokens, which remain stuck in the `IntentGatewayV2` contract balance with no accounting pointer to them. This is a direct loss-of-funds outcome for legitimate solvers/users interacting with any escrowed token that exhibits the return-false-on-failure behavior, satisfying the bounty's "stealing or loss of funds" / "false ... acceptance of a settlement as complete" impact class.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the order's escrowed token to belong to the (real but relatively rare) class of ERC20s that return `false` instead of reverting on transfer failure, and for that transfer to actually fail at withdrawal time (e.g. temporary pause/blacklist/rebasing edge case on the token side). No malicious peer, relayer, or admin is required — an ordinary user placing an order with such a token, or any transient failure condition on that token, triggers the silent-loss path purely through the protocol's own code path.

### Recommendation
Replace every raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` in `withdraw()`, the transaction-fee payout, and `SweepDust` handling with `SafeERC20.safeTransfer`, consistent with the `safeTransferFrom` already used for inbound transfers and consistent with the sibling non-Tron implementation (`IntentsBase._withdraw`) which correctly uses `IERC20(token).safeTransfer(beneficiary, amount)` [10](#0-9) .

### Proof of Concept
1. Deploy (or use) an ERC20 token `T` that implements `transfer` to return `false` (no revert) when an internal condition fails (e.g., a `paused` flag it can flip, or a blacklist).
2. Place an order with `T` as an input token via `placeOrder`; `T.transferFrom` succeeds normally (escrow funded, `_orders[commitment][T] = amount`).
3. Before the order is filled/cancelled, flip the condition on `T` so that a subsequent `transfer` call returns `false` instead of reverting (e.g., pause the token or blacklist the `IntentGatewayV2` contract or beneficiary — a legitimate token-admin action unrelated to any Hyperbridge actor).
4. Trigger `cancelOrder` (same-chain) or the cross-chain `RefundEscrow`/`RedeemEscrow` flow, which invokes `withdraw()`.
5. `token.call(...)` returns `success = true` (the call executed, just returned `false`), so the code does not revert; `_orders[commitment][T]` is decremented to zero and `_filled[commitment]` is set — the order is marked settled.
6. The beneficiary never received any `T` tokens (real balance unchanged), and no further withdrawal for this commitment is possible, permanently orphaning the escrowed `T` inside `IntentGatewayV2`.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L38-56)
```text
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";

import {IUniswapV2Router02} from "@uniswap/v2-periphery/contracts/interfaces/IUniswapV2Router02.sol";
import {ICallDispatcher, Call} from "../../../src/interfaces/ICallDispatcher.sol";


/**
 * @title IntentGatewayV2
 * @author Polytope Labs (hello@polytope.technology)
 *
 * Implements the IntentGatewayV2 contract for Tron
 *
 * @dev The IntentGateway allows for the creation and fulfillment of same-chain & cross-chain orders.
 */
contract IntentGatewayV2 is HyperApp, EIP712 {
    using SafeERC20 for IERC20;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L399-399)
```text
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L453-453)
```text
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L519-530)
```text
        if (isSameChain) {
            // Same-chain: validate locally and refund immediately
            // only owner can cancel
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

            // Verify we're on the correct chain
            if (orderSource != currentChain) revert WrongChain();

            WithdrawalRequest memory body =
                WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user});

            withdraw(body, true);
```

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L693-699)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L707-713)
```text
        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-734)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
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
