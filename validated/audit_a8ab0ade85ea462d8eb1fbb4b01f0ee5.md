## Analysis

The Tron variant of `IntentGatewayV2` (`evm/tron/contracts/apps/IntentGatewayV2.sol`) imports and uses OpenZeppelin's `SafeERC20` for the *deposit* side of escrow (`IERC20(token).safeTransferFrom(...)` at lines 453/478), which reverts if the return value is falsy or if the token has no code. [1](#0-0)  However, the *payout* side of the same contract — `withdraw()` and the `SweepDust` handler inside `onAccept` — bypasses that safety wrapper entirely and instead performs a raw low-level `.call()` to the token, checking only the boolean `success` returned by the call (whether it reverted) and never decoding/validating the ERC20 return-data boolean: [2](#0-1) [3](#0-2) 

### Title
Unchecked ERC20 return value in escrow `withdraw()` / `SweepDust` payout lets non-compliant tokens mark orders filled/refunded while sending zero funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`withdraw()` records the beneficiary and marks the commitment permanently settled (`_filled[body.commitment] = beneficiary;`) *before* moving funds, then pays out via `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))`, only reverting `if (!success)`. [4](#0-3)  Solidity's low-level `success` reflects only whether the call reverted — it is `true` both for a call to an address with no deployed code and for any ERC20 whose `transfer` returns `false` instead of reverting on failure (the exact class of non-standard tokens `SafeERC20` exists to guard against, and the same bug class as the reported solmate `safeTransfer`/`safeTransferFrom` issue). This same contract already applies `SafeERC20.safeTransferFrom` correctly on the deposit path, showing the payout path is an inconsistent, unguarded regression of the same primitive.

### Finding Description
- Escrow accounting is decremented (`_orders[body.commitment][token] -= amount;`) and the order is irreversibly marked filled/refunded regardless of whether the token transfer logically succeeded. [5](#0-4) 
- The identical pattern appears in the fee redemption right after (`feeToken.call(...)`) and in the `SweepDust` handler. [6](#0-5) [7](#0-6) 
- `withdraw()` is invoked from `onAccept` for the `RedeemEscrow`/`RefundEscrow` message kinds, which are one-shot cross-chain messages authenticated via `authenticate(incoming.request)` — there is no retry path once consumed. [8](#0-7) 
- The order's escrowed `token` addresses originate from user/solver-supplied `order.inputs[i].token` at order-creation time — any ERC20 that returns `false` on failure (rather than reverting) can be selected by the order creator, filler, or is simply a real-world non-compliant token already in use.
- Because `_filled[commitment]` is set unconditionally at the top of `withdraw()`, and the low-level call "succeeds" even when the transfer logically fails, the beneficiary is permanently recorded as paid while receiving zero tokens, and the commitment can never be redeemed again.

### Impact Explanation
This directly causes loss of funds for the legitimate beneficiary (order creator on refund, or filler/solver on redeem): escrowed tokens remain locked in the `IntentGatewayV2` contract forever while the protocol's own state (`_filled`, `_orders`) reports the transfer as completed and emits `EscrowReleased`/`EscrowRefunded`. This is a direct violation of the "bridged assets ... must move exactly once and only to the rightful beneficiary and amount" invariant, matching the required impact category of stealing/loss of funds via false state acceptance on the settlement path.

### Likelihood Explanation
No malicious relayer, prover, admin, or peer is required — the trigger is solely the choice of a non-standard ERC20 token (one that returns `false` instead of reverting on transfer failure) as an order's input/output/fee token, a property entirely determined by ordinary order data supplied by unprivileged order creators/solvers. The failure condition (e.g., a paused/blacklist-style token returning `false`) is realistic for real-world ERC20s and is exactly the scenario `SafeERC20` — already imported and used elsewhere in this very file — is designed to prevent.

### Recommendation
Replace all raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` payout patterns in `withdraw()`, the fee-redemption block, and `SweepDust` with `SafeERC20.safeTransfer`, consistent with the library's existing use for deposits (`safeTransferFrom`). This restores both the code-existence check and proper decoding of the ERC20 boolean return value before committing `_filled`/`_orders` state.

### Proof of Concept
1. An order is created with `order.inputs[i].token` set to an ERC20 whose `transfer` implementation can return `false` without reverting (e.g., paused/blacklist-gated token, or any legacy non-compliant ERC20). Escrow succeeds via `safeTransferFrom` (real deposit occurs).
2. Before redemption/refund executes, the token enters a state where `transfer(beneficiary, amount)` returns `false` (e.g., beneficiary blacklisted, or contract paused) instead of reverting.
3. The `RedeemEscrow`/`RefundEscrow` cross-chain message is delivered and processed normally through ISMP; `onAccept` calls `withdraw()`.
4. `withdraw()` sets `_filled[body.commitment] = beneficiary`, then calls `token.call(...)`. The call does not revert, so `success == true`, even though the token internally returned `false` and moved no funds.
5. `_orders[body.commitment][token] -= amount` executes, `EscrowReleased`/`EscrowRefunded` is emitted, and the commitment is permanently marked filled — the tokens remain stuck in the contract with no way to retry, while all protocol-level bookkeeping shows the beneficiary as paid.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-672)
```text
        } else if (kind == RequestKind.SweepDust) {
            SweepDust memory req = abi.decode(incoming.request.body[1:], (SweepDust));

            uint256 outputsLen = req.outputs.length;
            for (uint256 i; i < outputsLen;) {
                TokenInfo memory info = req.outputs[i];
                address token = address(uint160(uint256(info.token)));
                uint256 amount = info.amount;

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
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-714)
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
```
