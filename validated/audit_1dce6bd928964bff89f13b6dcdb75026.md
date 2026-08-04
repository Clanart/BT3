## Analysis

The `IntentGatewayV2` contract deployed for Tron (`evm/tron/contracts/apps/IntentGatewayV2.sol`) imports and uses `SafeERC20` for the deposit path (`IERC20(token).safeTransferFrom(...)`), which includes OpenZeppelin's contract-existence check. However, the withdrawal/settlement path abandons `SafeERC20` and instead performs raw low-level `.call()`s directly on the token address without any `EXTCODESIZE`/`code.length` check — exactly the pattern flagged in the external report.

### Title
Escrow withdrawal silently "succeeds" without transferring tokens when the token contract has no code - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
`withdraw()` and the `SweepDust` handler in `IntentGatewayV2` (Tron variant) release escrowed funds using raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` calls instead of `SafeERC20.safeTransfer`. A low-level `CALL` to an address with zero bytecode returns `success = true` with empty return data in the EVM/TVM, so the `if (!success) revert TransferFailed();` guard never triggers. Deposits into escrow use `safeTransferFrom` (which does check `code.length > 0`), but the withdrawal path has no equivalent check, breaking the deposit/withdrawal symmetry that the report calls out.

### Finding Description
In `withdraw()`: [1](#0-0) 
the code does:
```solidity
if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");
    if (!sent) revert InsufficientNativeToken();
} else {
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();
}
_orders[body.commitment][token] -= amount;
```
There is no `extcodesize(token)` check before the call, unlike `CallDispatcher.dispatch`, which explicitly guards against this: [2](#0-1) 

The same unguarded pattern is repeated for transaction-fee redemption and for the `SweepDust` handler inside `onAccept`: [3](#0-2) [4](#0-3) 

Meanwhile, deposits use `SafeERC20` (which does have a code-length check baked into `Address.functionCall`): [5](#0-4) [6](#0-5) 

If the token contract underlying an escrowed input becomes a non-contract address (e.g., destroyed via `SELFDESTRUCT`, or a proxy that becomes non-existent) after the deposit succeeded but before `withdraw` is invoked via the cross-chain `RedeemEscrow`/`RefundEscrow` flow, the raw `.call()` trivially "succeeds" against the empty-code address. `_orders[body.commitment][token] -= amount;` and `_filled[body.commitment] = beneficiary;` are still applied, permanently marking the order as filled/refunded even though the beneficiary received nothing. There is no fallback recovery path once `_orders[...]` and `_filled[...]` are updated.

### Impact Explanation
This directly matches the "bridged assets/escrow must move exactly once and only to the rightful beneficiary and amount" invariant. When the low-level call silently no-ops, the beneficiary loses the escrowed value permanently: the contract's internal accounting (`_orders`, `_filled`) records the funds as delivered/refunded, so no retry or alternate claim path exists. This is a fund-loss bug reachable through the ordinary settlement flow (`onAccept` → `withdraw`), not through a malicious relayer, prover, or admin action — it only requires a token whose contract can lose its bytecode between escrow deposit and redemption.

### Likelihood Explanation
Medium-low but real: it requires an escrowed input/fee token to become codeless before withdrawal is processed (e.g. via `SELFDESTRUCT`, a buggy/kill-switch token, or a counterfactual/CREATE2 proxy pattern). This is exactly the class of "destroyed token contract" scenario the external report explicitly calls out as its exploit scenario, and the code path is a real divergence from the safer pattern used everywhere else in the same contract (`SafeERC20`, `CallDispatcher`'s explicit `extcodesize` guard).

### Recommendation
- Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` calls in `withdraw()` and the `SweepDust` branch of `onAccept()` with `IERC20(token).safeTransfer(beneficiary, amount)` (already imported via `SafeERC20`), matching the deposit-side pattern and the fix already applied in the newer `IntentsBase.sol` (`IERC20(token).safeTransfer(beneficiary, amount);`, [7](#0-6) ).
- As defense-in-depth, add an explicit `extcodesize(token) > 0` check (as done in `CallDispatcher.dispatch`) before any low-level call to a token address across the codebase.

### Proof of Concept
1. User creates an order with `order.inputs[0].token = T` where `T` is a normal ERC20 with a legitimate `SELFDESTRUCT`-capable implementation (or a minimal proxy whose implementation can later vanish).
2. Solver fills the order; `safeTransferFrom` succeeds since `T` has code; `_orders[commitment][T] += amount`.
3. Token `T` is destroyed (loses bytecode) before settlement/redemption completes.
4. Cross-chain `RedeemEscrow`/`RefundEscrow` message arrives; `onAccept` calls `withdraw(body, ...)`.
5. `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` against codeless `T` returns `(true, "")` — no revert.
6. `_orders[commitment][T] -= amount` and `_filled[commitment] = beneficiary` are committed; the order is now permanently marked settled, but `beneficiary` never received any tokens — the escrowed value is lost with no recovery mechanism.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L398-399)
```text
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-673)
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
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
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

**File:** evm/src/utils/CallDispatcher.sol (L44-60)
```text
    function dispatch(bytes memory encoded) external {
        Call[] memory calls = abi.decode(encoded, (Call[]));
        uint256 callsLen = calls.length;
        for (uint256 i = 0; i < callsLen; ++i) {
            Call memory call = calls[i];
            uint32 size;
            address to = call.to;
            assembly {
                size := extcodesize(to)
            }

            if (size == 0) {
                revert NotContract(to);
            }

            (bool success, bytes memory result) = to.call{value: call.value}(call.data);
            if (!success) revert CallFailed(to, result);
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
