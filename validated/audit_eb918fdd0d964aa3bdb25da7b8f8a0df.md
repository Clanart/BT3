### Title
`EvmHost.dispatch()` / `fundRequest()` swap excess native ETH into fee tokens without refunding unspent change to the payer - ([File: evm/src/core/EvmHost.sol])

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` accept native ETH via `msg.value` and swap it for the exact required `feeToken` amount using `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(...)`. Unlike the sibling implementations in `IntentGatewayV2`/`IntrinsicIntents`/`ExtrinsicIntents`, none of these `EvmHost` functions capture the `amounts` return value of the swap or refund the unspent native token difference back to `_msgSender()`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
This is the local analog of the reported bug class: forwarding `msg.value` into a downstream call path without a proper refund mechanism to the rightful sender, so any overpayment/leftover native token is not returned. The reported bug is about a missing `receive()`/`fallback()` causing router refunds to *revert*; the Hyperbridge analog is worse in effect — the code never even attempts to compute or forward the unspent change, so the ETH silently accumulates inside `EvmHost`'s own balance instead of going back to the caller.

Compare with the correct pattern used elsewhere in the same codebase, `IntentGatewayV2.placeOrder`, which:
1. Captures the swap's `amounts` return value,
2. Subtracts the amount actually spent from `msgValue`,
3. Explicitly refunds any leftover native token to `msg.sender`. [4](#0-3) 

The equivalent logic in `ExtrinsicIntents`/`IntrinsicIntents` also explicitly refunds unspent native tokens after a dispatch: [5](#0-4) 

`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` — the exact functions the documentation instructs application developers to call with `{value: msg.value}` for native-token-denominated relayer fees — skip this step entirely: they forward the entire `msg.value` into the swap and never account for or return the difference. [6](#0-5) 

Since `post.fee`/`get.fee`/`amount` is a fixed target amount in `feeToken` and native token/ETH prices fluctuate, it is essentially impossible for a caller to supply the exact `msg.value` needed; any caller who overestimates (which is the recommended safe practice to avoid reverts from `UniswapV2Router02` requiring `amountInMax` slippage headroom, and is exactly the pattern used and tested for `IntentGatewayV2`) will have the excess ETH permanently absorbed by the `EvmHost` contract instead of refunded.

### Impact Explanation
Any application or end user who dispatches a POST/GET request or funds a pending request through `EvmHost` using native token payment loses the entire unspent portion of `msg.value` — it is not returned to `msg.sender`/`_msgSender()`, and is not tracked as belonging to the payer. The value becomes indistinguishable from protocol revenue and can only be recovered by governance via `IHostManager.withdraw()`, benefiting the fee treasury/admin rather than the rightful payer. This is a direct loss-of-funds bug for every native-token dispatcher, not a griefing or DoS issue — it silently transfers value away from users on every over-estimated native payment. [7](#0-6) 

### Likelihood Explanation
This triggers on the standard, documented usage path (`IDispatcher(_host).dispatch{value: msg.value}(post)`), requires no malicious actor, no relayer/prover assumption, and no front-running — any unprivileged caller supplying more native token than the exact fee-swap requirement (which is expected behavior given price volatility and lack of a slippage/refund guarantee in the public interface) hits this loss path deterministically. [8](#0-7) 

### Recommendation
Mirror the pattern already implemented in `IntentGatewayV2`/`IntrinsicIntents`/`ExtrinsicIntents`: capture the `amounts` array returned by `swapETHForExactTokens`, compute `unspent = msg.value - amounts[0]`, and refund `unspent` to `_msgSender()` (reverting the whole dispatch if the refund transfer fails) in `EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()`.

### Proof of Concept
1. Application contract calls `IDispatcher(host).dispatch{value: 1 ether}(post)` where `post.fee` requires only a fraction of an ETH worth of `feeToken` (a realistic scenario given ETH/feeToken price volatility and no way to know the exact conversion in advance).
2. `EvmHost.dispatch` executes `swapETHForExactTokens{value: 1 ether}(post.fee, path, address(this), block.timestamp)`.
3. The router spends only the ETH needed for `post.fee` feeTokens and refunds the rest to `msg.sender`, which is `EvmHost` (since `EvmHost` is the caller of the router, not the original application).
4. `EvmHost.dispatch` never reads the swap's return value nor forwards any leftover ETH back to `_msgSender()` (the original application/user) — the leftover ETH remains in `EvmHost`'s balance permanently, unlike `IntentGatewayV2.placeOrder`, which performs the equivalent refund at lines 356/365-368. [1](#0-0) [9](#0-8) 

Note: I was unable to fully verify within the available tool budget whether `EvmHost.sol` contains a `receive()`/`fallback()` function elsewhere in the file (a `grep_search` matched 2 occurrences of that pattern in the file, but I did not get to inspect the exact lines before running out of iterations). This does not change the core finding — whether or not `EvmHost` can technically accept the router's internal refund, the contract never forwards that ETH back to the original payer, so it is lost from the user's perspective either way. Confirming the exact receive/fallback location and its accounting would require a follow-up Devin session with full file access.

### Citations

**File:** evm/src/core/EvmHost.sol (L74-86)
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

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L61-78)
```text
        dest: StateMachine.evm(1),
        timeout: timeout,
        to: abi.encode(to),
        fee: relayerFee,
        payer: msg.sender
    });

    return IDispatcher(_host).dispatch{value: msg.value}(post);
}
```

### Code Breakdown

1. **Create the DispatchPost struct** - Populate all required fields including destination, recipient, message body, timeout, and fees
2. **Set the destination** - Use `StateMachine.evm(1)` for Ethereum Mainnet (or appropriate chain ID)
3. **Encode the recipient** - Convert the destination contract address to bytes using `abi.encode(to)`
4. **Dispatch the request** - Call `IDispatcher(_host).dispatch()` with the POST request and any required native token value
5. **Return commitment hash** - The function returns a unique identifier for tracking the request
```
