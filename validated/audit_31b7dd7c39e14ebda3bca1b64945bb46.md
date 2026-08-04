## Analysis

The external report's core broken invariant: **when swapping native currency to cover a fee, the contract forwards the caller's entire `msg.value` to a swap function but never reconciles the actual amount consumed against what the caller sent — the leftover is neither correctly refunded nor accounted for**, breaking the flow for legitimate callers.

I traced the equivalent native→fee-token swap pattern across Hyperbridge's EVM contracts and found the same amount-reconciliation defect, but manifesting as **permanent fund loss instead of revert**, in `EvmHost.sol`.

### Comparison of the two code paths

`IntentGatewayV2.placeOrder` correctly reconciles `msg.value` against what the Uniswap swap actually consumes and refunds the difference to the caller: [1](#0-0) 

`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` perform the identical native→feeToken swap via `swapETHForExactTokens{value: msg.value}(...)`, but **never capture the `amounts[0]` actually spent, never track a running `msgValue`, and never refund the unspent portion to the caller**: [2](#0-1) [3](#0-2) [4](#0-3) 

The router (or the local wrapper) does refund excess ETH — but it refunds it to `msg.sender` of the swap call, which is `EvmHost` itself, not the original dispatcher: [5](#0-4) 

So any ETH sent beyond what the swap actually needed lands in the `EvmHost` contract balance and is permanently unrecoverable by the caller. The docs even confirm that off-chain fee estimation via `quote()` is imprecise and sandwich-prone, meaning over-sending is an expected, common occurrence: [6](#0-5) 

### Title
Excess native token sent to `EvmHost.dispatch()`/`fundRequest()` for fee-swap payment is permanently trapped, not refunded to caller - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` accept `msg.value` and swap it for `post.fee`/`get.fee`/`amount` worth of fee tokens via `swapETHForExactTokens`. Unlike `IntentGatewayV2.placeOrder`, which correctly tracks the amount consumed (`amounts[0]`) and refunds the unspent remainder to `msg.sender`, these three `EvmHost` functions discard any unspent native token, which the underlying router/wrapper returns to `EvmHost` itself rather than to the original caller.

### Finding Description
In `dispatch(DispatchPost)`:
```solidity
if (msg.value > 0) {
    ...
    IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
        post.fee, path, address(this), block.timestamp
    );
}
```
`swapETHForExactTokens` only consumes as much ETH as needed to buy exactly `post.fee` fee tokens and refunds the rest to its caller — but the caller of the router is `EvmHost`, not the end user. The refunded ETH is added to `EvmHost`'s own balance. `EvmHost` never captures this refund, never compares it to `msg.value`, and never forwards anything back to `_msgSender()`. The identical pattern exists in `dispatch(DispatchGet)` and `fundRequest()`.

This is the same broken invariant as the ZETA bug in the external report: an amount actually required by an internal swap diverges from the value the caller supplied, and the difference is not correctly reconciled. In the DODO case the mismatch caused a revert; here it causes silent, irreversible fund loss because the difference is swallowed by the contract instead of being returned.

### Impact Explanation
Because `quote()`-based off-chain fee estimation is inherently imprecise (subject to slippage and price movement, as the project's own documentation warns), any caller who supplies more native token than the exact amount the swap ends up consuming will have the surplus permanently locked inside `EvmHost`, with no view function, refund path, or governance sweep shown for this specific surplus. This is a direct, unconditional loss of user funds on every overestimated `dispatch()`/`fundRequest()` call paid in native token — it requires no attacker, relayer, or privileged actor, only a normal user paying fees via the native-token path exactly as the public API intends.

### Likelihood Explanation
High. Overestimating `msg.value` when quoting fees off-chain is the expected, documented mode of operation for any dApp integrating `dispatch()`/`fundRequest()` with the native-payment path; exact quote precision cannot be guaranteed given AMM price movement between estimation and execution. Every such call that slightly overpays permanently loses the difference.

### Recommendation
Mirror the pattern already implemented in `IntentGatewayV2.placeOrder`/`fillOrder`: capture `amounts[0]` returned by `swapETHForExactTokens`, compute the unspent remainder (`msg.value - amounts[0]`), and refund it to `_msgSender()` (or `post.payer`/`get` caller as appropriate) before returning from `dispatch()` and `fundRequest()`.

### Proof of Concept
1. User calls `EvmHost.dispatch(DispatchPost)` with `post.fee = 100` fee-token units and sends `msg.value = 1 ether` (a conservative overestimate to guard against slippage, as recommended by the docs). [2](#0-1) 
2. `swapETHForExactTokens` only needs, say, `0.4 ether` to buy the 100 fee-token units; it refunds `0.6 ether` to its caller, `EvmHost`. [7](#0-6) 
3. `dispatch()` never reads or forwards this `0.6 ether`; it stays in `EvmHost`'s balance permanently, with the user having no recorded claim to it (contrast with `IntentGatewayV2.placeOrder`'s explicit `msgValue -= amounts[0]` + refund at the end of the function). [1](#0-0) 
4. Repeating this across all `dispatch()`/`fundRequest()` calls accumulates unrecoverable native token in `EvmHost`.

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

**File:** evm/src/core/EvmHost.sol (L921-932)
```text
    function dispatch(DispatchPost memory post) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                post.fee, path, address(this), block.timestamp
            );
        } else if (post.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), post.fee);
        }
```

**File:** evm/src/core/EvmHost.sol (L974-985)
```text
    function dispatch(DispatchGet memory get) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                get.fee, path, address(this), block.timestamp
            );
        } else if (get.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), get.fee);
        }
```

**File:** evm/src/core/EvmHost.sol (L1031-1042)
```text
    function fundRequest(bytes32 commitment, uint256 amount) external payable notFrozen {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                amount, path, address(this), block.timestamp
            );
        } else {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), amount);
        }
```

**File:** evm/src/utils/uniswapv2/UniV4UniswapV2Wrapper.sol (L83-101)
```text
        // Snapshot standing balance (excluding inbound msg.value) so the refund is the swap-call delta only,
        // immune to any ETH that lands on the wrapper from outside the router (e.g., selfdestruct, coinbase).
        uint256 balanceBefore = address(this).balance - msg.value;

        IUniversalRouter(_params.universalRouter).execute{value: msg.value}(
            abi.encodePacked(bytes1(uint8(Commands.V4_SWAP))), inputs, deadline
        );

        uint256 refundETH = address(this).balance - balanceBefore;

        if (refundETH > 0) {
            (bool success,) = msg.sender.call{value: refundETH}("");
            require(success, "ETH refund failed");
        }

        amounts = new uint256[](2);
        amounts[0] = msg.value - refundETH;
        amounts[1] = amountOut;
    }
```

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L236-238)
```text
<Callout type="warning" title="Estimate Fees Off-Chain">
Use the `quote()` view function from your frontend to estimate how much native token users need to send. **Do not call `quote()` in smart contract transactions.** It uses Uniswap's `getAmountsIn`, making it vulnerable to sandwich attacks. Only use it off-chain for frontend fee estimation
</Callout>
```
