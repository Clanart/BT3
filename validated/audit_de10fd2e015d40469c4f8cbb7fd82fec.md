## Finding: Overpaid native-token fees permanently trapped in `EvmHost` instead of refunded to the caller [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Overpaid `msg.value` in `EvmHost.dispatch()`/`fundRequest()` is swept into the router refund path but never returned to the caller, permanently locking excess native ETH - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` each forward the **entire** `msg.value` into `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(exactFee, path, address(this), block.timestamp)` without first validating that `msg.value` equals (or capping it to) the exact fee required. This is the same broken invariant as the Pyth report: an exact-output payable call is fed an unvalidated `msg.value`.

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
The same pattern repeats in `dispatch(DispatchGet)` (`get.fee`) and `fundRequest()` (`amount`). None of these functions checks `msg.value == requiredFee` beforehand, mirroring exactly the missing-validation pattern flagged in the Pyth report.

The Uniswap V2 router's `swapETHForExactTokens` does refund unused ETH — but it refunds to `msg.sender` **of the swap call**, which here is `EvmHost` itself (since `EvmHost` is the direct caller of the router), not the external account that called `EvmHost.dispatch()`/`fundRequest()`. Consequently:
- Any ETH sent to `EvmHost.dispatch()`/`fundRequest()` beyond the exact fee required is swapped, refunded by the router back into `EvmHost`'s own balance, and then simply **retained by the contract** — `EvmHost` never forwards that residual ETH back to the original caller.
- Compare this to `IntentGatewayV2`/`ExtrinsicIntents`, where the exact same overpayment scenario is explicitly handled: `msgValue` is tracked and any leftover is refunded with `msg.sender.call{value: msgValue}("")` at the end of `placeOrder`/`fillOrder`: [4](#0-3) [5](#0-4) 

`EvmHost` has no equivalent refund-to-caller step, meaning users who overestimate the native fee for a dispatch permanently lose the excess into the Host contract's balance (recoverable, if at all, only by governance/hostManager withdrawal — not by the paying user).

### Impact Explanation
This is a direct, unprivileged loss-of-funds path reachable through the public, unauthenticated `dispatch()`/`fundRequest()` entrypoints that every `HyperApp` and external caller uses to pay for cross-chain messages. Any user or integrating contract that overestimates the native/ETH amount needed to cover `post.fee`/`get.fee`/`amount` loses the difference with no recovery mechanism, satisfying the bounty's "stealing or loss of funds" impact class.

### Likelihood Explanation
Medium-to-high: `post.fee`/`get.fee` amounts are set by the caller/app logic and can easily diverge from the swap-computed requirement (e.g., conservative overestimation for safety, or fee change between quote and execution), and there is no built-in incentive or warning preventing overpayment on these routinely-called dispatch paths.

### Recommendation
Before performing the swap, compute the exact input required for the exact output (`IUniswapV2Router02.getAmountsIn(fee, path)` or check `pyth`-style `getUpdateFee`-equivalent) and either:
1. `require(msg.value == requiredAmountIn)` and revert otherwise, or
2. After the swap, explicitly refund any leftover `msg.value` (i.e., `address(this).balance` delta, or `msg.value - amounts[0]`) back to `_msgSender()` in `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`, mirroring the pattern already implemented in `IntentGatewayV2.sol` and `ExtrinsicIntents.sol`.

### Proof of Concept
1. Configure `_hostParams.uniswapV2` to a standard `UniswapV2Router02` (or the repo's `UniV4UniswapV2Wrapper`).
2. Call `EvmHost.dispatch(DispatchPost({..., fee: 1e18, ...}))` with `msg.value = 2 ether`, where only a fraction of 1 ether of ETH is actually needed to buy `1e18` feeToken.
3. The router computes `amounts[0]` (ETH actually needed) and refunds `msg.value - amounts[0]` — but to `msg.sender` of the swap call, i.e., `EvmHost`, per `swapETHForExactTokens{value: msg.value}(post.fee, path, address(this), block.timestamp)` at [6](#0-5) .
4. `EvmHost`'s ETH balance increases by the refunded dust; the calling user's balance is never credited back — confirm via balance diff: user pays the full `2 ether`, receives nothing back, while `address(EvmHost).balance` increases by the unused portion.

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
