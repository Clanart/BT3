## Finding [1](#0-0) 

The bug-class analog from the Pendle report ("output/return value from a swap call is mishandled, corrupting downstream accounting") maps directly onto `IntentGatewayV2.placeOrder()` in the Tron variant of the contract, which drops the swap's return value entirely and — unlike the canonical EVM contract — never refunds the caller's unspent native value.

### Title
Unspent native ETH is permanently stranded in `IntentGatewayV2.placeOrder()` on the Tron deployment - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The canonical EVM `IntentGatewayV2.placeOrder()` tracks a local `msgValue` counter, decrements it by the actual ETH consumed by the fee swap (`amounts[0]` returned from `swapETHForExactTokens`), and refunds any leftover `msgValue` back to `msg.sender` at the end of the function: [2](#0-1) 

The Tron variant performs the same exact-output swap but discards the returned `amounts` array completely and has no corresponding refund block anywhere in `placeOrder()`: [3](#0-2) 

### Finding Description
`swapETHForExactTokens(order.fees, path, address(this), block.timestamp)` is an exact-output swap: the router computes the exact ETH required, executes the swap, and — per standard UniswapV2Router semantics — refunds any excess `msg.value` back to the caller (which is the `IntentGatewayV2` contract itself, since it forwards `{value: msgValue}`). In the EVM version this refunded excess is accounted for via `msgValue -= amounts[0]` and ultimately returned to the user in the final refund block. In the Tron version, the return value is never captured, and critically, `placeOrder()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` has no "refund unspent native tokens" step at all (compare the full function body to the EVM version, which explicitly ends with a native-token refund to `msg.sender`).

Consequently, if a user calls `placeOrder()` with `order.fees > 0` and sends more native value than strictly required (e.g., any ETH not consumed by native `order.inputs` and not consumed by the exact-output fee swap), that surplus is refunded by the router into the `IntentGatewayV2` contract balance but is never tracked, credited to the order's escrow, or returned to the user.

### Impact Explanation
This is a direct, unprivileged loss of funds: any ordinary user overpaying native value on `placeOrder()` (a completely normal client-side rounding/slippage-buffer scenario) has that excess permanently locked in the contract with no code path to reclaim it. This matches the bounty's "stealing or loss of funds" impact category and requires no malicious peer, relayer, or admin — it is triggered by the contract's own faulty accounting, exactly analogous to the external report's core issue of a swap's return values being mishandled and causing the wrong amount to be tracked/paid.

### Likelihood Explanation
High. `order.fees > 0` is a normal configuration (protocol fee-token payments described in the docs), and users naturally send `msg.value` with some buffer above the exact quoted requirement, especially since the exact fee-token cost depends on live AMM pricing at execution time. Every such call silently strands the difference.

### Recommendation
Port the EVM contract's `msgValue` tracking and end-of-function native refund logic into the Tron `IntentGatewayV2.placeOrder()`: capture the `amounts` returned by `swapETHForExactTokens`, decrement `msgValue` by `amounts[0]`, and refund any remaining `msgValue` to `msg.sender` before emitting `OrderPlaced`.

### Proof of Concept
1. On the Tron deployment, configure a destination with `order.fees > 0` and `feeToken` swappable via the configured `uniswapV2Router`.
2. Call `placeOrder()` with only ERC20 `order.inputs` (no native input) but send `msg.value` equal to, say, 2x the ETH actually required to buy `order.fees` worth of fee token.
3. The `swapETHForExactTokens` call executes, the router refunds the unused ~1x surplus ETH back to the `IntentGatewayV2` contract address, but no code in the function ever forwards it to `msg.sender`.
4. `placeOrder()` completes successfully and emits `OrderPlaced`; the surplus ETH remains in the contract's balance indefinitely with no sweep or refund mechanism reachable by the user.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L465-497)
```text
        if (order.fees > 0) {
            // escrow fees
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }

        emit OrderPlaced({
            user: order.user,
            source: order.source,
            destination: order.destination,
            deadline: order.deadline,
            nonce: order.nonce,
            fees: order.fees,
            session: order.session,
            predispatch: order.predispatch.assets,
            inputs: reducedInputs,
            beneficiary: order.output.beneficiary,
            outputs: order.output.assets
        });
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L345-368)
```text
        if (order.fees > 0) {
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
                msgValue -= amounts[0];
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }

        // Refund any unspent native tokens to the user.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```
