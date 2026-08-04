## Finding: `EvmHost.dispatch()` / `fundRequest()` never refund excess native-token payment to the caller

### Title
Excess native-token payment on `EvmHost.dispatch()`/`fundRequest()` is silently trapped in the Host instead of being refunded to the caller - (`evm/src/core/EvmHost.sol`)

### Summary
The C4 report's broken invariant is: a payable entrypoint validates that `msg.value` covers a required amount but keeps the entire `msg.value`, including any excess, instead of refunding the difference to the caller. `EvmHost.sol` reproduces this exact pattern in its core dispatch functions, which are the primary payment path for every `HyperApp` on Hyperbridge.

### Finding Description
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` accept native token payment and swap it for the exact required fee via Uniswap V2: [1](#0-0) 

```solidity
function dispatch(DispatchPost memory post) external payable notFrozen returns (bytes32 commitment) {
    if (msg.value > 0) {
        address[] memory path = new address[](2);
        address uniswapV2 = _hostParams.uniswapV2;
        path[0] = IUniswapV2Router02(uniswapV2).WETH();
        path[1] = feeToken();
        IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
            post.fee, path, address(this), block.timestamp
        );
```

`swapETHForExactTokens` is called with `amountInMax = msg.value` and a fixed `amountOut = post.fee`. Uniswap V2's router only spends what is required to produce exactly `post.fee` output and refunds any unspent ETH — but that refund goes to `msg.sender` of the router call, which is `EvmHost` itself, not the original caller of `dispatch()`. `EvmHost.dispatch()` never forwards that refunded excess back to `_msgSender()`. The identical pattern repeats in `dispatch(DispatchGet)` and `fundRequest()`: [2](#0-1) [3](#0-2) 

This is exactly the bug class from the external report — a payable function validates sufficiency of payment (implicitly, via `amountInMax`) but keeps the excess rather than returning it.

Notably, every downstream `HyperApp` in this same repo that wraps native payment through the Host has already had to implement its own explicit excess-refund bookkeeping to compensate for this gap — e.g. `IntentGatewayV2.placeOrder()` tracks `msgValue -= amounts[0]` after the swap and refunds the remainder to `msg.sender`: [4](#0-3) 

and `ExtrinsicIntents._fillOrder` does the same for solvers: [5](#0-4) 

But any `HyperApp` that follows the documented pattern of forwarding `msg.value` directly to `IDispatcher(host()).dispatch{value: msg.value}(request)` — exactly as shown in Hyperbridge's own developer docs — inherits this gap, because `EvmHost` performs no refund of its own: [6](#0-5) 

The excess ETH accumulates as a native balance sitting in `EvmHost`, which is only withdrawable by the privileged `hostManager`/governance path via `IHostManager.withdraw` — never by the caller who overpaid: [7](#0-6) 

### Impact Explanation
Every unprivileged caller who dispatches a native-token-paid POST/GET request or funds a request loses any amount by which `msg.value` exceeds the actual Uniswap-computed cost of `post.fee`/`get.fee`/`amount`. This happens routinely, not just from user error: Uniswap spot prices move between the moment a client estimates `quote()` off-chain and the moment the transaction executes on-chain, and callers reasonably pad `msg.value` for slippage exactly as the Hyperbridge docs themselves recommend ("User must send enough native tokens to cover fees"). The lost ETH is not burned — it accrues to the Host contract balance and becomes sweepable only through the governance-gated `withdraw` path, meaning ordinary users/apps permanently lose funds with no recovery mechanism at the entrypoint level. This is a direct, protocol-wide loss-of-funds bug matching the bounty's "loss of funds" impact category.

### Likelihood Explanation
High likelihood: this triggers on the default/documented native-payment code path for `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` — used by essentially any app that doesn't pay in the fee token — any time the caller's `msg.value` isn't the exact minimal amount the AMM ends up consuming (which is the common case due to price movement/slippage buffering, not an edge case).

### Recommendation
Mirror the pattern already implemented in `IntentGatewayV2`/`ExtrinsicIntents`: capture the `amounts[0]` actually spent from `swapETHForExactTokens` (or track `address(this).balance` before/after) in `EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`, and forward any unspent `msg.value` back to `_msgSender()` before returning.

### Proof of Concept
1. A `HyperApp` (or an EOA integrator following the official docs) calls `IDispatcher(host()).dispatch{value: X}(post)` where `X` is intentionally padded above the currently quoted fee to tolerate slippage.
2. Inside `EvmHost.dispatch()`, `swapETHForExactTokens{value: X}(post.fee, path, address(this), block.timestamp)` spends only `amountIn <= X` and refunds `X - amountIn` to `address(this)` (the Host), per Uniswap V2 Router semantics.
3. `EvmHost.dispatch()` returns without ever transferring `X - amountIn` back to the caller; the request/commitment is recorded and the function completes normally — no revert, no signal that funds were retained.
4. The `X - amountIn` remainder is now permanently part of the Host's native balance, recoverable only by the `hostManager` via the governance-gated `IHostManager.withdraw` path, never by the caller who supplied it.

### Citations

**File:** evm/src/core/EvmHost.sol (L74-96)
```text
interface IHostManager {
    /**
     * @dev Updates IsmpHost params
     * @param params new IsmpHost params
     */
    function updateHostParams(HostParams memory params) external;

    /**
     * @dev withdraws bridge revenue to the given address
     * @param params, the parameters for withdrawal
     */
    function withdraw(WithdrawParams memory params) external;
}

// Withdrawal parameters
struct WithdrawParams {
    // The beneficiary address
    address beneficiary;
    // the amount to be disbursed
    uint256 amount;
    // Withdraw the native token?
    address token;
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

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L162-187)
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
```
