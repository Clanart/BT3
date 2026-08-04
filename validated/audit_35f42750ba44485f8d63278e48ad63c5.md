Based on my investigation, I found a concrete local analog in `EvmHost.sol`'s native-token fee payment path.

### Title
Excess native token sent to `EvmHost.dispatch()`/`fundRequest()` is trapped with no refund path - (File: `evm/src/core/EvmHost.sol`)

### Summary
The Aave bug's core broken invariant was: a payment flow splits `msg.value` between two purposes, but the excess/remainder is never returned to the payer, causing fund loss (or revert). `EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` exhibit the same broken invariant: they forward the caller's entire `msg.value` into a Uniswap `swapETHForExactTokens` call, but never refund the unspent remainder to the original caller.

### Finding Description
In `EvmHost.dispatch(DispatchPost)`: [1](#0-0) 

and identically in `dispatch(DispatchGet)`: [2](#0-1) 

and in `fundRequest`: [3](#0-2) 

All three call `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(fee, path, address(this), block.timestamp)`. Uniswap V2's `swapETHForExactTokens` computes the exact ETH input required for `fee` output tokens and refunds any unused ETH — but it refunds it to `msg.sender` of that call, which is `EvmHost` itself (since `EvmHost` is the caller of the router), not the original end user who called `dispatch`/`fundRequest`. The refunded dust accumulates as plain ETH balance inside `EvmHost`.

Unlike this code, the sibling `IntentGatewayV2.placeOrder` and `ExtrinsicIntents._fillCrossChain` explicitly track a local `msgValue` variable, subtract only what was consumed, and refund the leftover to `msg.sender` at the end: [4](#0-3) [5](#0-4) 

`EvmHost.dispatch`/`fundRequest` have no equivalent refund logic, and I found no `receive()`, `withdraw`, or sweep function in `EvmHost.sol` that would let a user or the protocol recover this trapped native ETH.

### Impact Explanation
Any unprivileged caller who sends more native token than the exact amount consumed by the Uniswap swap (which is a normal/expected condition since callers must estimate the fee amount in advance and price can move, or callers intentionally send a safety margin as shown in the docs' guidance to overpay and expect a refund) permanently loses the difference — it becomes stuck ETH inside `EvmHost` with no recovery mechanism. This is a direct loss-of-funds bug reachable by any ordinary user of the public `dispatch`/`fundRequest` entrypoints, with no relayer, prover, or admin involved.

### Likelihood Explanation
High. `dispatch()` and `fundRequest()` are the primary payable entrypoints applications/users call to pay for message dispatch in the native token, and the documentation itself instructs users to send `msg.value` covering the fee with an expectation of "automatic" handling. Since `swapETHForExactTokens` naturally leaves dust (due to slippage buffers or price movement between quoting and execution), triggering the loss requires no adversarial conditions — only routine usage.

### Recommendation
Track the pre-call balance (or use the swap's returned `amounts[0]`) and explicitly refund `msg.value - amounts[0]` back to `_msgSender()` in `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest`, mirroring the pattern already implemented in `IntentGatewayV2.placeOrder` and `ExtrinsicIntents._fillCrossChain`.

### Proof of Concept
1. User calls `EvmHost.dispatch(DispatchPost)` with `post.fee = 100` (fee-token units) and `msg.value = 1 ether` (a safety margin above the actual current swap cost, e.g. because the quoted price shifts by the time the tx lands).
2. `swapETHForExactTokens{value: 1 ether}(100, path, address(this), ...)` consumes only the ETH needed to buy exactly 100 fee tokens (e.g., 0.01 ETH) and refunds the remaining ~0.99 ETH to `msg.sender` of the swap call, i.e., to `EvmHost` itself.
3. `dispatch` returns normally; the request is created successfully.
4. The 0.99 ETH remains in `EvmHost`'s balance permanently — the original caller receives nothing back, and there is no function in `EvmHost.sol` to withdraw or reclaim this stranded native balance.

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

**File:** evm/src/apps/IntentGatewayV2.sol (L364-368)
```text
        // Refund any unspent native tokens to the user.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L164-168)
```text
        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```
