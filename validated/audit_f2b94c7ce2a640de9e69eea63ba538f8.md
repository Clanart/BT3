### Title
Excess native-token payment in `EvmHost.dispatch`/`fundRequest` is refunded to the Host contract instead of the caller, permanently locking ETH - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest` all accept native-token payment and swap it for the fee token via Uniswap V2, but none of them refund unspent native token back to the original caller — the swap's leftover ETH is refunded to `EvmHost` itself (the immediate caller of the router), not to `_msgSender()`.

### Finding Description
In all three payable entrypoints, when `msg.value > 0` the code calls `swapETHForExactTokens{value: msg.value}(feeAmount, path, address(this), block.timestamp)`: [1](#0-0) [2](#0-1) [3](#0-2) 

`swapETHForExactTokens` only consumes exactly `feeAmount` worth of ETH (`post.fee`, `get.fee`, or `amount`) and refunds any unused ETH — but that refund is sent to `msg.sender` of the *router call*, which is `EvmHost` (since `EvmHost` itself is the account funding the swap with `{value: msg.value}`). There is no code afterward in any of these three functions that forwards the leftover balance back to `_msgSender()` (the actual user who paid). Compare this to the fully-guarded pattern used elsewhere in the same codebase — `IntentGatewayV2.placeOrder`, `IntrinsicIntents._fillSameChain`, and `ExtrinsicIntents.fillOrder` all explicitly track `msgValue` and issue a refund `.call{value: msgValue}("")` to `msg.sender` for any unspent native token: [4](#0-3) [5](#0-4) [6](#0-5) 

`EvmHost.dispatch`/`fundRequest` have no equivalent refund step, so ETH sent beyond the exact amount needed to buy `post.fee`/`get.fee`/`amount` worth of fee token is captured by the router as change, sent back to `EvmHost`'s own balance, and left with no accounting or path back to the payer — mirroring the exact "sanity-check" failure class in the referenced Sublime `PoolFactory` report where the wrong branch silently drops the caller's `msg.value`.

### Impact Explanation
Any caller who over-estimates the ETH needed for a POST/GET dispatch or a `fundRequest` top-up (which is expected, since the exact Uniswap price is unknown at call time and users must send enough headroom to guarantee the swap succeeds) has the excess ETH permanently absorbed into `EvmHost`'s balance instead of being returned. This is direct user fund loss with no recovery mechanism visible in the reviewed `EvmHost` code (the `withdraw`-style functions in this file are tied to `feeToken` bridge revenue accounting via `HostManager`, not to arbitrary native-ETH balance sweep, so the locked ETH cannot be retrieved through the normal governance/host-manager `withdraw` path either).

### Likelihood Explanation
This requires no privileged actor, relayer, prover, or malicious peer — it is triggered by any unprivileged user calling a completely standard, documented native-token payment flow (`dispatch{value: msg.value}(...)`, as literally shown in the project's own docs) whenever they send more ETH than the exact Uniswap output price requires, which is normal/expected behavior since users must include slippage/price buffer. It's a routine usage pattern, not an edge case, making likelihood high.

### Recommendation
After the `swapETHForExactTokens` call in `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest`, capture the `amounts[0]` actually spent (as `IntentGatewayV2.placeOrder` already does at line 353-356) and refund `msg.value - amounts[0]` back to `_msgSender()` via a low-level call, reverting if the refund fails.

### Proof of Concept
1. A user calls `EvmHost.dispatch{value: 1 ether}(post)` where `post.fee = 100e18` (fee-token units), intending to leave headroom against price movement.
2. `dispatch` executes `swapETHForExactTokens{value: 1 ether}(100e18, [WETH, feeToken], address(this), deadline)`.
3. Suppose only `0.4 ether` is required to buy exactly `100e18` fee tokens at the current pool price; Uniswap's router refunds the remaining `0.6 ether` to `msg.sender` of the swap call, i.e., to `EvmHost`.
4. `dispatch` returns normally; the `0.6 ether` now sits in `EvmHost`'s balance with no state variable tracking it as belonging to the user, and no function in `EvmHost` sweeps arbitrary native ETH back to callers.
5. The user has permanently lost `0.6 ether` with no path to reclaim it, matching the "Ether locked without a way to retrieve it" bug class from the seed report.

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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L144-148)
```text
        // Refund any unspent native tokens to the solver.
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
