### Title
EvmHost dispatch/fundRequest native-token overpayment is never refunded to the caller - ([File: evm/src/core/EvmHost.sol])

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` accept `msg.value` and swap it through Uniswap V2 for the exact `feeToken` amount needed, but never capture or refund the leftover native token to the caller. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
Each of these functions is `external payable` and forwards the entire `msg.value` to `IUniswapV2Router02.swapETHForExactTokens(fee, path, address(this), block.timestamp)`, requesting only `post.fee` / `get.fee` / `amount` worth of the feeToken. The router's actual behavior (or the local `UniswapV2Wrapper`s used on some chains, e.g. `UniV3UniswapV2Wrapper.sol` line 143-149) computes the real amount spent and refunds any unspent ETH — but that refund is sent to `msg.sender` of the swap call, which is `EvmHost` itself, not the original transaction caller (`_msgSender()`). `EvmHost.dispatch`/`fundRequest` discard the `amounts` return value entirely and perform no subsequent transfer back to the caller. [4](#0-3) 

This is exactly the bug class from the external report: a function accepts a payment (fee) in native ETH, the user can send more than required, and the excess is silently absorbed by the contract instead of being returned. The codebase demonstrably knows this pattern is dangerous and fixes it correctly elsewhere — `IntentGatewayV2.placeOrder` and `ExtrinsicIntents._fillOnDestination` explicitly track `msgValue -= amounts[0]` after the swap and refund any remainder to `msg.sender` — but the core `IsmpHost` implementation in `EvmHost.sol` does not apply the same fix to its own native-fee dispatch path. [5](#0-4) [6](#0-5) 

Once stuck, the excess ETH-turned-feeToken sits in `EvmHost`'s balance and is only recoverable through the privileged `withdraw()` path gated to the `hostManager`, which can send it to any `beneficiary` chosen by governance — never automatically back to the user who overpaid. [7](#0-6) 

### Impact Explanation
Any unprivileged user calling `dispatch()` (directly, or transitively via `HyperApp.dispatchWithFeeToken`/native helpers documented in the SDK) or `fundRequest()` with `msg.value` greater than the exact quoted fee permanently loses the difference. There is no reentrancy needed and no privileged actor required — this is a direct, unconditional loss of funds for any caller who overestimates gas/fee costs (a common and encouraged practice per the docs' own guidance to "cover gas costs... typically 10-20% markup"). This matches the bounty's "stealing or loss of funds" impact category. [8](#0-7) 

### Likelihood Explanation
High. `dispatch()` is a core, frequently-invoked public entrypoint used by every app that pays relayer fees in native token, and the documentation explicitly instructs integrators to send a markup over the estimated relayer fee, virtually guaranteeing overpayment in normal operation — not an edge case. `fundRequest()` has the identical pattern.

### Recommendation
Mirror the pattern already used in `IntentGatewayV2.sol` and `ExtrinsicIntents.sol`: capture the `amounts` array returned by `swapETHForExactTokens`, compute `msg.value - amounts[0]`, and refund the remainder to `_msgSender()` (with reentrancy-safe ordering, e.g. checks-effects-interactions or a reentrancy guard) in `EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`.

### Proof of Concept
1. Caller invokes `EvmHost.dispatch{value: 2 ether}(post)` where `post.fee` only requires `0.01 ether` worth of feeToken via swap.
2. Router receives `2 ether`, deposits it, swaps out `amounts[0] ≈ 0.01 ether` worth, and refunds `~1.99 ether` to `msg.sender` — but `msg.sender` as seen by the router is `EvmHost`, not the caller.
3. `EvmHost.dispatch` never reads `amounts` or forwards anything back to `_msgSender()`; the request is dispatched and the function returns.
4. The `1.99 ether` (as native ETH refunded to `EvmHost`, or WETH remaining on `EvmHost` on Gnosis-style wrappers) is now stuck in `EvmHost`'s balance, recoverable only via governance's `withdraw()` to an address of governance's choosing, not the original caller. [1](#0-0)

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

**File:** evm/src/utils/uniswapv2/UniV3UniswapV2Wrapper.sol (L140-149)
```text
        bytes[] memory results = IMulticallExtended(_params.swapRouter).multicall(deadline, data);
        uint256 spent = abi.decode(results[0], (uint256));

        if (spent < msg.value) {
            uint256 refund = msg.value - spent;
            IWETH(weth).withdraw(refund);

            (bool success,) = msg.sender.call{value: refund}("");
            if (!success) revert RefundFailed();
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

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L103-113)
```text
**Relayer Fee Calculation:**

The relayer fee should cover:
1. **Gas costs on destination** - This includes:
   - **Proof verification** (~150k gas) - Fixed cost for verifying state proofs on the destination chain
   - **Execution gas** - Gas consumed by your contract's `IApp.onAccept` handler. 

2. **Relayer service fee** - Incentive for relayer services (typically 10-20% markup on gas costs)

**Refund on Timeout:**
If the request times out, the `payer` address receives the relayer fee back.
```
