## Title
Native-token overpayment lost (never refunded) in `EvmHost.dispatch`/`fundRequest` due to unchecked `swapETHForExactTokens` return value - (File: `evm/src/core/EvmHost.sol`)

## Summary
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest` all pay dispatch fees by calling Uniswap V2's `swapETHForExactTokens{value: msg.value}(fee, path, address(this), block.timestamp)` but discard the returned `amounts` array and never refund the unspent native token to the caller. This is the same bug class as the report's root cause — mishandling of a swap's structured output (there, wrong struct field/hardcoded param; here, an ignored return value) that lets value leak out of the intended flow at swap time.

## Finding Description
Uniswap V2's `swapETHForExactTokens` is an exact-output swap: the caller sends `msg.value` as the maximum input, and the router internally refunds any unspent ETH directly to `msg.sender` — which, in this call chain, is the `EvmHost` contract itself, not the original end user. `EvmHost` never captures the `amounts` return value nor forwards a refund back to `_msgSender()`: [1](#0-0) [2](#0-1) [3](#0-2) 

By contrast, `IntentGatewayV2.sol`'s `fillOrder`/order-placement path performs the identical Uniswap V2 exact-output swap but correctly captures `amounts[0]` and refunds the unspent native token to `msg.sender`: [4](#0-3) 

This shows the codebase's own pattern for correct handling exists elsewhere, but `EvmHost` — the core dispatch entrypoint used by every app/message sender that pays fees in native token — omits it.

The docs even acknowledge that on-chain quoting is imprecise and MEV-exposed, meaning users routinely send a `msg.value` computed off-chain (via `quote()`/`quoteNative()`) that will not exactly match the amount the router consumes at execution time: [5](#0-4) 

Any positive difference between the estimated fee-in-native and the actual amount the router spends (due to normal price movement, provided buffer margins, or sandwich activity) is swept by the router to `EvmHost`'s own balance and is not attributable or returned to the paying user.

## Impact Explanation
This is a genuine, unconditional loss of user funds on the core dispatch path (`dispatch`, `fundRequest`) — not a griefing or MEV/relayer-only issue. Every unprivileged caller who pays dispatch/response fees with native token and sends a `msg.value` with any margin above the exact amount the router consumes permanently loses that difference: it becomes indistinguishable protocol-owned ETH inside `EvmHost`, not a refund to the sender. Given SDK/docs explicitly recommend adding a buffer (e.g., "1% buffer" language elsewhere in fee quoting) when estimating native fees off-chain, this loss is not an edge case but an expected, recurring by-product of normal fee estimation and price movement between quote and execution.

## Likelihood Explanation
High. This triggers on every `dispatch()`/`fundRequest()` call paid in native token where `msg.value` is not exactly equal to the router's realized input amount — which is effectively every call, since exact price-matching between off-chain estimate and on-chain execution price is not guaranteed. No malicious relayer, prover, or governance actor is required; a single honest, unprivileged user calling `dispatch{value: ...}()` triggers the loss.

## Recommendation
Capture the `amounts` array returned by `swapETHForExactTokens` in `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest`, and refund `msg.value - amounts[0]` back to `_msgSender()`, mirroring the pattern already implemented in `IntentGatewayV2.sol`'s escrow-crediting logic.

## Proof of Concept
1. Compute `nativeFee` off-chain via `quote()`/`quoteNative()` as documented, and add any margin/buffer for safety.
2. Call `IDispatcher(host).dispatch{value: nativeFee}(post)` where `post.fee` requires strictly less input than `nativeFee` at execution time (e.g., price moved favorably, or the caller intentionally overpaid for safety margin).
3. Internally, `EvmHost.dispatch` calls `swapETHForExactTokens{value: nativeFee}(post.fee, path, address(this), block.timestamp)`. The router consumes `amounts[0] < nativeFee` and refunds `nativeFee - amounts[0]` ETH to `msg.sender`, i.e., to the `EvmHost` contract.
4. `EvmHost.dispatch` never reads `amounts` or forwards any refund to the original caller; the leftover ETH remains permanently in `EvmHost`'s balance, unattributed to the paying user, unlike the correctly-implemented refund path in `IntentGatewayV2.sol`'s `fillOrder`. [1](#0-0) [4](#0-3)

### Citations

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

**File:** evm/src/core/EvmHost.sol (L1031-1039)
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

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L236-238)
```text
<Callout type="warning" title="Estimate Fees Off-Chain">
Use the `quote()` view function from your frontend to estimate how much native token users need to send. **Do not call `quote()` in smart contract transactions.** It uses Uniswap's `getAmountsIn`, making it vulnerable to sandwich attacks. Only use it off-chain for frontend fee estimation
</Callout>
```
