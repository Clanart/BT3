### Title
Unchecked ERC-20 return value in `IntentGatewayV2.withdraw()` / `SweepDust` accepts failed transfers as success, permanently burning escrow accounting - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron build of `IntentGatewayV2` settles escrow withdrawals, refunds, and dust sweeps with a raw low-level `.call()` to the ERC-20 `transfer` selector, checking only that the call did not revert (`success`) and never decoding/validating the returned boolean.

### Finding Description
`withdraw()` (called from `onAccept` for `RedeemEscrow`/`RefundEscrow`, and from `onGetResponse` after a GET-timeout-driven refund) and the `SweepDust` branch of `onAccept` both do: [1](#0-0) [2](#0-1) [3](#0-2) 

This is the inverse-but-equivalent failure mode of the seed report: instead of a `require(bool)` reverting on tokens with no return value, here a token that returns `false` on failure (rather than reverting) makes the low-level call itself succeed (`success == true`), because EVM/TVM low-level `.call` only reports `false` on revert, not on a logically-failed operation that returns `false`. The code treats that as a completed transfer:
- In `withdraw()`, `_orders[body.commitment][token] -= amount;` is executed unconditionally after the unchecked `success` check, and `_filled[body.commitment] = beneficiary;` is already set before any transfer is attempted at all — so even a definitively failed transfer marks the order filled and depletes escrow accounting.
- The same pattern applies to the fee-token payout and to `SweepDust`.

Contrast this with the canonical EVM (non-Tron) implementation, `evm/src/apps/intentsv2/IntentsBase.sol`, and other flows in this same repo such as `IntentGatewayV2.sol` (`evm/src/apps/IntentGatewayV2.sol`) and `EvmHost.sol`, which consistently use OpenZeppelin's `safeTransfer`/`safeTransferFrom`: [4](#0-3) [5](#0-4) 

`SafeERC20` is even imported and aliased in the Tron file (`using SafeERC20 for IERC20;`), but bypassed specifically in the withdrawal/sweep code paths, confirming this is an inconsistency/regression rather than intentional design: [6](#0-5) 

### Impact Explanation
This breaks the "bridged assets ... must move exactly once and only to the rightful beneficiary and amount" invariant. If the escrowed `token` is a non-standard ERC20 that returns `false` instead of reverting on failure (e.g., insufficient balance edge cases, blacklist/pausable tokens, or any token whose `transfer` implementation returns `false` on failure), then:
- `withdraw()` will decrement `_orders[commitment][token]` and mark `_filled[commitment] = beneficiary` as if the payout succeeded, even though the beneficiary received nothing.
- The escrowed tokens remain stuck in the contract with no code path to reclaim them, since `_orders[commitment][token]` has already been zeroed/decremented and `UnknownOrder()` will fire on any retry.
- This is a permanent loss of the depositor's/solver's escrowed funds and a false "settlement" being recorded on-chain (escrow marked released/refunded while no value moved) — a direct violation of the "false proof/state acceptance" and "loss of funds" impact categories, reachable purely by a solver/order-creator choosing (or being forced to accept) such a token as the intent input/output asset, with no privileged actor required.

### Likelihood Explanation
This is reachable through the normal `onAccept`/`onGetResponse` message flow that the host authenticates and dispatches for every cross-chain intent settlement (`RedeemEscrow`, `RefundEscrow`, GET-timeout refund, and governance-triggered `SweepDust`), so any intent that uses a non-reverting-on-failure ERC20 as its input/output token will trip this path. It requires no relayer, prover, or admin misbehavior — only that one of the involved tokens has this common non-standard behavior, which is exactly the class of tokens the original report calls out (and which the rest of the codebase already defends against via `SafeERC20`).

### Recommendation
Replace the raw `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` + `success`-only check in `withdraw()` (escrow token payout and fee-token payout) and in the `SweepDust` branch of `onAccept` with `IERC20(token).safeTransfer(beneficiary, amount)`, consistent with the rest of the codebase (`EvmHost.sol`, `evm/src/apps/IntentGatewayV2.sol`, `evm/src/apps/intentsv2/IntentsBase.sol`). Since `using SafeERC20 for IERC20;` is already declared in this file, this is a drop-in fix.

### Proof of Concept
1. Deploy (or use) an ERC20 token whose `transfer()` returns `false` on failure instead of reverting (a legally-valid ERC20 behavior per spec, and a documented real-world pattern the seed report itself cites, e.g. some ZRX-style tokens).
2. Create and fill an intent order using this token as an input, so `_orders[commitment][token] = amount` is escrowed in `IntentGatewayV2`.
3. Cause the underlying `transfer()` call to logically fail while still not reverting (e.g., token-specific business logic returning `false`, or the contract balance being drained via `SweepDust`/dust-sweep race before withdrawal executes).
4. Hyperbridge delivers a `RedeemEscrow`/`RefundEscrow` request to `onAccept`, which calls `withdraw()`: [7](#0-6) 
5. `token.call(...)` returns `(true, abi.encode(false))` — `success` is `true` because the call itself didn't revert — so `TransferFailed()` is never raised, `_orders[commitment][token]` is decremented to zero, and `_filled[commitment]` is set, even though the beneficiary's token balance never changed.
6. The escrowed tokens are now permanently stranded in the `IntentGatewayV2` contract: no further withdrawal is possible because `_orders[commitment][token] == 0` triggers `UnknownOrder()` on any retry, and no admin/sweep path is exposed to return these tokens to the rightful beneficiary.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L664-667)
```text
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L693-701)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
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

**File:** evm/src/core/EvmHost.sol (L841-846)
```text
        // reward the relayer fee
        uint256 fee = _requestCommitments[commitment].fee;
        if (fee != 0) {
            IERC20(feeToken()).safeTransfer(relayer, fee);
        }
        emit GetRequestHandled({commitment: commitment, relayer: relayer});
```

**File:** evm/src/apps/IntentGatewayV2.sol (L218-220)
```text
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }
```
