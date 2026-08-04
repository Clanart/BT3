Found a solid local analog: the Tron variant of `IntentGatewayV2.placeOrder` mishandles the native-ETH-to-fee-token swap refund path, causing permanent loss of user funds.

### Title
`IntentGatewayV2.placeOrder` (Tron variant) permanently locks overpaid native ETH during the fee swap instead of refunding it to the user - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
When a user places an order and pays `order.fees` with native token (rather than the fee token directly), the contract swaps the leftover `msgValue` for the exact fee-token amount via Uniswap V2's `swapETHForExactTokens`. The canonical EVM implementation captures the swap's actual ETH cost and refunds the remainder to `msg.sender`. The Tron implementation of the same function drops both the return value capture and the final refund step entirely, so any native token sent above what the input escrow + exact fee swap consume is stranded in the contract.

### Finding Description
In the reference EVM contract, `placeOrder` explicitly tracks unspent `msgValue` and refunds it at the end: [1](#0-0) 

The fee-swap branch there captures `amounts[0]` (actual wei spent by the router) and decrements `msgValue` accordingly, and finally refunds any leftover native token to `msg.sender`: [2](#0-1) 

The Tron deployment of `IntentGatewayV2.placeOrder`, however, performs the identical fee-swap when `msgValue > 0`, but never captures the router's return value, never decrements `msgValue`, and — critically — the function ends immediately after emitting `OrderPlaced` with no refund logic at all: [3](#0-2) 

`IUniswapV2Router02.swapETHForExactTokens` only spends the exact ETH required to receive `order.fees` output tokens and refunds the remainder — but that refund lands on the caller of the swap, which is the `IntentGatewayV2` contract itself, not the original user. Since the Tron `placeOrder` supplies the full remaining `msgValue` to the swap call (`{value: msgValue}`) and has no logic afterward to forward the router's refund (or any leftover `msgValue`) back to `msg.sender`, that ETH is stuck inside the gateway contract with no accounting entry (`_orders[commitment][...]`) tying it back to the user.

This directly mirrors the M-20 bug class: a native-ETH code path that fails to correctly track/forward value through a swap, causing funds sent by an unprivileged caller to be misrouted/lost rather than reaching their rightful destination.

### Impact Explanation
Any user on the Tron deployment who overpays native token for the `order.fees` swap (which is expected/likely, since callers must estimate the ETH cost of a Uniswap swap off-chain and typically add a buffer, exactly as documented for the EVM path) permanently loses the excess ETH. There is no compensating escrow entry, no event, and no withdrawal path for the user to reclaim it — it becomes gateway balance with no linked accounting, effectively unauthorized loss of user funds within a production bridge contract.

### Likelihood Explanation
This triggers on the ordinary, documented usage pattern (pay `order.fees` with native token) — no malicious peer, relayer, or governance actor needed. The SDK/docs already recommend sending `nativeValue` computed off-chain, which will almost never equal the on-chain swap cost exactly, so overpayment (and thus loss) is the common case rather than an edge case.

### Recommendation
Mirror the EVM implementation in the Tron contract: capture `swapETHForExactTokens`'s returned `amounts[0]`, decrement `msgValue` by it, and add the same trailing refund block that forwards any leftover `msgValue` back to `msg.sender` before `placeOrder` returns.

### Proof of Concept
1. User calls `placeOrder{value: X}(order, graffiti)` on the Tron `IntentGatewayV2`, where `order.fees > 0` and `X` exceeds the ETH needed to buy `order.fees` worth of fee token (e.g., pads for slippage, or inputs are ERC-20 so all of `X` is meant for the fee swap).
2. `msgValue` computed from `msg.value` at line 382 is untouched by the input-escrow branch when inputs are ERC20 (`msgValue` stays at `X`).
3. At line 468, since `msgValue > 0`, the contract calls `swapETHForExactTokens{value: msgValue}(order.fees, ...)`, supplying the entire `X`, not just the needed portion.
4. The router spends only enough ETH to buy `order.fees` tokens and refunds the rest to `address(this)` (the gateway) automatically.
5. Function ends (line 484-497) with no refund to `msg.sender` — the excess ETH is now permanently part of the gateway's balance, unlinked to any escrow or user-claimable state. [4](#0-3)

### Citations

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
