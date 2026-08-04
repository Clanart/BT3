### Title
Excess native ETH sent to `EvmHost.dispatch()`/`fundRequest()` for fee-token swaps is permanently stuck in the Host contract instead of being refunded to the caller - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost` accepts native token payment for POST/GET dispatches and `fundRequest`, swapping it to `feeToken` via `swapETHForExactTokens`. This Uniswap V2 call refunds any *unused* input ETH to `msg.sender` — but from the router's perspective `msg.sender` is `EvmHost` itself, not the external caller who funded the transaction. `EvmHost` never forwards that refund onward, so any excess native token sent beyond what the swap actually consumes accumulates permanently in the Host contract balance, mirroring the "excess ETH stuck in Fee Manager" bug class from the seed report.

### Finding Description
In `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest`, when `msg.value > 0` the Host swaps native token for the exact fee amount: [1](#0-0) 

and identically for GET requests: [2](#0-1) 

and for `fundRequest`: [3](#0-2) 

`swapETHForExactTokens(amountOut, path, to, deadline)` is `payable`; the router computes the exact ETH input required to obtain `amountOut` and refunds any leftover ETH to whoever called the router — which, in this call chain, is `EvmHost`, since `EvmHost` itself invokes `IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(...)`. The refund therefore lands on `address(this)` (the Host), not on the original transaction sender. No subsequent line in any of these three functions checks `address(this).balance` or forwards a residual amount back to `_msgSender()`/`post.payer`.

This is structurally identical to the seed bug: a contract forwards the *entire* `msg.value` into an inner fee-collection step that only consumes part of it, and the surplus is captured by that inner mechanism (the Fee Manager in the seed report; the Uniswap router → Host self-refund here) instead of returning to the caller.

The protocol's own documentation confirms exact-amount estimation is inherently imprecise and meant only as an off-chain hint, guaranteeing that real-world callers will routinely overpay: [4](#0-3) 

and shows the exact vulnerable call pattern recommended to integrators: [5](#0-4) 

Contrast this with `IntentGatewayV2`'s intent-settlement paths, which explicitly track and refund any unspent native token to the caller after dispatch-related operations: [6](#0-5) 

That refund only returns whatever `IntentGatewayV2` itself did not forward to `EvmHost.dispatch`. It has no visibility into, and cannot recover, the leftover ETH that the router refunds *inside* `EvmHost` after the swap — that portion is a second, hidden buffer of excess funds that never leaves the Host contract. `EvmHost.sol` exposes no `receive`/`withdraw`/sweep mechanism for stray native ETH accumulated this way (only `IERC20(feeToken()).safeTransfer` refunds for relayer-fee ERC20 amounts on timeout), so any such leftover is permanently locked, benefiting neither the user, the relayer, nor the protocol treasury.

### Impact Explanation
Every native-token-funded `dispatch`/`fundRequest` call that doesn't send the *exact* wei amount required by the live AMM price (which is effectively guaranteed, since the docs explicitly forbid on-chain `quote()` calls and push developers to add slippage buffers) leaks the difference into `EvmHost`'s balance with no recovery path for the depositor. Over the life of the protocol across every EVM deployment, this is a continuous fund-loss/fund-lock condition for any unprivileged user or app who pays fees in native token — directly matching the bounty's "loss of funds" impact category.

### Likelihood Explanation
High. Any caller using the native-token payment path documented as the recommended pattern (`IDispatcher(host()).dispatch{value: msg.value}(post)`) is affected whenever `msg.value` exceeds the router's exact required input — which is the normal, expected case any time a buffer is added for slippage/price movement, or when `feeToken`/native prices shift between fee estimation (off-chain `quote()`) and execution. No malicious actor, relayer, or governance compromise is needed; a normal user calling a documented entrypoint triggers the loss.

### Recommendation
After the `swapETHForExactTokens` call in `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest`, compare `address(this).balance` (or track `msg.value` minus the router's `amounts[0]` actually spent) and refund any residual ETH to `_msgSender()` (or `post.payer`/the appropriate depositor) via a low-level call, consistent with the refund pattern already used elsewhere in the codebase (e.g., `evm/src/apps/intentsv2/ExtrinsicIntents.sol` lines 157-168, and `evm/src/apps/IntentGatewayV2.sol` lines 364-368).

### Proof of Concept
1. A user (or `HyperApp`-based integrator) calls `EvmHost.dispatch(DispatchPost)` with `post.fee = X` fee-token units and sends `msg.value = Y` native token, where `Y` includes a reasonable slippage buffer above the current on-chain quote for `X`.
2. `EvmHost.dispatch` executes `IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: Y}(X, path, address(this), block.timestamp)` — see `evm/src/core/EvmHost.sol:921-932`.
3. The router only needs `Z < Y` wei to produce `X` fee tokens; it refunds `Y - Z` to `msg.sender` of the swap call, which is `EvmHost` (not the user).
4. `EvmHost.dispatch` returns without inspecting or forwarding the refunded `Y - Z`; the request proceeds normally and emits `PostRequestEvent`.
5. `address(EvmHost).balance` permanently increases by `Y - Z`; no function in `EvmHost.sol` allows the depositor (or anyone) to recover this amount.

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

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L162-188)
```text
### Native Token Payment

For native token payments, dispatch directly and let the Host handle the Uniswap swap:

```solidity lineNumbers title="MyApp.sol"
contract MyApp is HyperApp {
    function sendMessageWithNative(
        bytes memory message,
        bytes memory dest,
        uint64 timeout,
        address to,
        uint256 relayerFee
    ) public payable returns (bytes32) {
        DispatchPost memory post = DispatchPost({
            body: message,
            dest: dest,
            timeout: timeout,
            to: abi.encode(to),
            fee: relayerFee,
            payer: msg.sender
        });
        
        // User must send enough native tokens to cover fees
        // The Host will swap native -> feeToken via Uniswap
        return IDispatcher(host()).dispatch{value: msg.value}(post);
    }
}
```

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L236-238)
```text
<Callout type="warning" title="Estimate Fees Off-Chain">
Use the `quote()` view function from your frontend to estimate how much native token users need to send. **Do not call `quote()` in smart contract transactions.** It uses Uniswap's `getAmountsIn`, making it vulnerable to sandwich attacks. Only use it off-chain for frontend fee estimation
</Callout>
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L157-168)
```text
        if (options.nativeDispatchFee > 0 && msgValue >= options.nativeDispatchFee) {
            IDispatcher(hostAddr).dispatch{value: options.nativeDispatchFee}(request);
            msgValue -= options.nativeDispatchFee;
        } else {
            dispatchWithFeeToken(request);
        }

        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```
