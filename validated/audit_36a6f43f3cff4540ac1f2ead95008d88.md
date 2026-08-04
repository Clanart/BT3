## Title
Excess Native Token Payment Permanently Locked in Tron `IntentGatewayV2.placeOrder()` Fee Swap - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
In the Tron variant of `IntentGatewayV2.sol`, `placeOrder()` forwards the *entire* remaining `msgValue` to Uniswap's `swapETHForExactTokens` when paying `order.fees` in native token, but — unlike the canonical EVM implementation — never accounts for the dust ETH refunded by the router, nor refunds any leftover native token to the caller. Any excess native token a user sends above the exact amount required is silently swallowed into the contract's balance and permanently unrecoverable by the user, mirroring the reported bug class where a broader contextual value (`msg.value`/current-transaction value) is used in place of the precisely intended sub-amount, causing an incorrect and unaccounted-for fund flow.

### Finding Description
`placeOrder()` tracks `msgValue = msg.value` and spends portions of it on input transfers and, when `order.fees > 0`, on a Uniswap swap for the fee token: [1](#0-0) 

```solidity
if (order.fees > 0) {
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
```

`swapETHForExactTokens` is called with `value: msgValue` — the *entire* leftover native balance the user sent — rather than the exact amount required to purchase `order.fees` worth of fee token. Uniswap V2's router computes `amounts[0]` (exact input needed) and refunds `msg.value - amounts[0]` back to the caller, which is the `IntentGatewayV2` contract itself (not the end user). The function then falls straight through to `emit OrderPlaced(...)` with no `msgValue -= amounts[0]` bookkeeping and no refund step to `msg.sender`.

This is the exact analog of the reported CREATE bug class: a downstream call is supplied with a broader/contextual value (`msgValue`, standing in for the full "current transaction" value) instead of the precise designated sub-amount, and the resulting discrepancy (the dust) is misdirected — here, trapped in the contract instead of returned to its rightful owner.

Contrast with the maintained EVM implementation, which correctly tracks and refunds the dust: [2](#0-1) 

```solidity
uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
    order.fees, path, address(this), block.timestamp
);
msgValue -= amounts[0];
...
// Refund any unspent native tokens to the user.
if (msgValue > 0) {
    (bool sent,) = msg.sender.call{value: msgValue}("");
    if (!sent) revert InsufficientNativeToken();
}
```

The Tron contract has neither the `msgValue -= amounts[0]` adjustment nor the final refund block, so any native-token overpayment during `placeOrder()` is unconditionally lost to the user.

### Impact Explanation
Any ordinary, unprivileged user who calls `placeOrder()` on the Tron gateway with `order.fees > 0` and pays the fee in native token loses the difference between the native value they supplied and the exact amount Uniswap needed to buy `order.fees` worth of fee token. Since users generally cannot predict the exact swap input amount in advance (it depends on live pool reserves), essentially every native-fee order placement leaks funds into the contract, with no path for the depositor to reclaim it. This is a direct, protocol-caused loss of user funds during ordinary, non-adversarial use of a public entrypoint — squarely within the "stealing or loss of funds" impact category.

### Likelihood Explanation
Likelihood is high: this triggers on every call to `placeOrder()` that pays fees in native token where the caller doesn't send the exact router-computed input amount (which is the normal case, since callers typically send a safety margin of native value). No malicious actor, relayer, or governance action is required — it is a deterministic consequence of normal usage of the public, unprivileged `placeOrder()` function.

### Recommendation
Mirror the fix already present in the canonical EVM `IntentGatewayV2.sol`: capture the `amounts` array returned by `swapETHForExactTokens`, deduct `amounts[0]` from `msgValue`, and refund any remaining `msgValue` to `msg.sender` at the end of `placeOrder()`.

### Proof of Concept
1. User calls `placeOrder(order, graffiti)` on the Tron `IntentGatewayV2` with `order.fees = 100` (fee-token units) and no `order.inputs` requiring native token, sending `msg.value = 1 ETH` intending to cover the fee swap with margin.
2. Inside `placeOrder()`, `msgValue = 1 ETH`. The fee branch calls `swapETHForExactTokens{value: 1 ETH}(100, path, address(this), block.timestamp)`.
3. Uniswap computes the exact input needed, e.g. `amounts[0] = 0.01 ETH`, executes the swap, and refunds `0.99 ETH` back to `address(this)` (the `IntentGatewayV2` contract) — not to the user.
4. `placeOrder()` proceeds directly to `emit OrderPlaced(...)` with no adjustment to `msgValue` and no refund call.
5. The user's `0.99 ETH` surplus is now part of the contract's native balance, unassociated with any order or accounting entry, and unrecoverable by the user through any contract function.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L465-482)
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
