### Title
Native-token overpayment on `EvmHost.dispatch()` / `fundRequest()` is silently trapped in the host contract instead of being refunded to the caller - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` all accept native-token payment and internally swap it for the fee token via `swapETHForExactTokens{value: msg.value}(fee, ...)`. Unlike the sibling contract `IntentGatewayV2.sol`, which captures the router's returned `amounts[0]` and refunds the unspent `msgValue` to `msg.sender`, `EvmHost` discards the return value entirely and never forwards the leftover native token back to the caller. Any ETH sent in excess of the exact swap-in amount is retained by `EvmHost` with no code path that returns it to the payer, matching exactly the bug class in the external report ("extra ether sent cannot be recovered by the user").

### Finding Description
In `EvmHost.dispatch(DispatchPost)`: [1](#0-0) 

the call is:
```solidity
IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
    post.fee, path, address(this), block.timestamp
);
```
The Uniswap V2 router's `swapETHForExactTokens` computes the exact input amount needed to buy `post.fee` tokens and refunds any `msg.value` surplus to *its caller* — which here is `EvmHost` itself (`address(this)` in `EvmHost`), not the original transaction sender. The return value (`amounts[0]`, the exact ETH spent) is not captured, and there is no subsequent step that computes `msg.value - amounts[0]` and forwards it back to `_msgSender()`.

The identical pattern repeats in `dispatch(DispatchGet)` [2](#0-1)  and in `fundRequest()` [3](#0-2) .

Contrast this with the correct pattern already implemented elsewhere in the same repository, `IntentGatewayV2.placeOrder`, which explicitly tracks and refunds the unspent value: [4](#0-3) 

`EvmHost` has no equivalent refund step. Any user (or any `HyperApp`/app contract forwarding `msg.value`) who slightly overestimates the native-token fee required for `dispatch()`/`fundRequest()` — which the documentation explicitly instructs callers to do off-chain via `quote()`, warning that on-chain estimation is sandwich-attack prone — will have the excess ETH permanently absorbed into `EvmHost`'s balance with no user-facing withdrawal path. This is functionally identical to the reported "mintFounderHero()" bug: `msg.value` is not constrained to equal the exact required amount, and there is no mechanism to return the difference to the payer.

### Impact Explanation
This is a direct loss-of-funds bug for any ordinary user or integrating contract calling `EvmHost.dispatch()` / `fundRequest()` with native token payment, which the docs explicitly recommend as a supported payment method (`docs/content/developers/evm/messaging/post-requests.mdx`, `get-requests.mdx`). Because slippage/price movement between fee estimation and execution is expected and unavoidable (the docs themselves warn about sandwich-attack risk in on-chain quoting), any overestimation permanently locks the surplus ETH in the `EvmHost` contract with no code path to reclaim it — it becomes stuck host-contract balance, unlike the analogous `IntentGatewayV2` flow where the same situation is explicitly handled with a refund. This satisfies the bounty's "loss of funds" impact category and requires no privileged actor, malicious relayer, or compromised prover — a completely ordinary, well-intentioned caller triggers it.

### Likelihood Explanation
High likelihood: this occurs on the ordinary, unprivileged, most common call path (any `dispatch()`/`fundRequest()` invocation with native payment), and requires no adversarial conditions — only normal price movement between off-chain fee quoting and on-chain execution (which the docs acknowledge is expected). Every native-token payer of `dispatch`/`fundRequest` is exposed.

### Recommendation
Mirror the pattern already used in `IntentGatewayV2.sol`: capture the `amounts` array returned by `swapETHForExactTokens` in all three `EvmHost` functions (`dispatch(DispatchPost)`, `dispatch(DispatchGet)`, `fundRequest`), compute the unspent native amount (`msg.value - amounts[0]`), and forward it back to `_msgSender()` (or the designated payer) via a low-level call, reverting on failure, exactly as done in `evm/src/apps/IntentGatewayV2.sol:353-368`.

### Proof of Concept
1. Attacker/user (or any integrating `HyperApp`) calls `EvmHost.dispatch(post)` with `post.fee = 100e6` (fee-token units) and sends `msg.value = 1 ether`, intending only enough ETH to cover a fee-token swap that in reality only costs `0.01 ether` at current pool price.
2. `EvmHost.dispatch` executes `swapETHForExactTokens{value: 1 ether}(100e6, [WETH, feeToken], address(this), block.timestamp)`.
3. The router spends only `~0.01 ether`, refunds `~0.99 ether` back to `msg.sender` of the router call — which is `EvmHost`, not the original caller.
4. `EvmHost.dispatch` never reads the router's return value or forwards any refund; the `~0.99 ether` surplus remains in `EvmHost`'s balance permanently, with no view or withdraw function reachable by the original payer to reclaim it.
5. Repeat for any `fundRequest()` or `dispatch(DispatchGet)` call with native overpayment — same result.

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
