## Title
`EvmHost.dispatch()`/`dispatch(GetRequest)`/`fundRequest()` never refund excess native `msg.value` after the Uniswap swap, permanently stranding user funds in the host contract — ([File: evm/src/core/EvmHost.sol])

### Summary
This is a direct local analog of the FeeBuyback `submit()` bug: a payable function that accepts `msg.value` as payment for a target `amount`, but never verifies/reconciles the two, so any excess native token supplied by the caller is lost rather than returned to the payer.

### Finding Description
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` all follow the same native-payment pattern: if `msg.value > 0`, the entire `msg.value` is forwarded to `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(fee/amount, path, address(this), block.timestamp)`. [1](#0-0) [2](#0-1) [3](#0-2) 

A real `UniswapV2Router02.swapETHForExactTokens` only spends up to the exact `amountOut` worth of ETH and refunds the unspent portion via `TransferHelper.safeTransferETH(msg.sender, ...)`. Critically, from the router's perspective, `msg.sender` is `EvmHost` itself (the router is called by `EvmHost`, not by the original transaction sender). So any refund from the router lands back on `EvmHost`'s own balance — it is never forwarded to the actual caller who supplied the excess `msg.value`.

None of `dispatch()`, `dispatch(GetRequest)`, or `fundRequest()` capture the router's return value or the resulting leftover ETH balance to refund it to `_msgSender()`/`post.payer`. This is unlike other places in the same codebase (`IntentGatewayV2.sol`, `ExtrinsicIntents.sol`, `IntrinsicIntents.sol`) which explicitly track `msgValue -= amounts[0]` and refund any unspent native token back to `msg.sender` at the end of the function. [4](#0-3) 

In `EvmHost`, the stranded ETH simply becomes part of the contract's native balance. The only mechanism that can move native token out of `EvmHost` is `withdraw(WithdrawParams)`, which is restricted to `_hostParams.hostManager` (cross-chain governance) and sends it to an arbitrary `beneficiary` — not back to the original overpaying user. [5](#0-4) 

### Impact Explanation
Any unprivileged user (or app contract) dispatching a POST/GET request or funding a request with native token, who supplies `msg.value` even slightly above what the Uniswap swap consumes to obtain the exact fee token amount (which is normal, since callers must overestimate due to slippage/price movement between quote-time and execution-time, and the docs explicitly warn `quote()` is only an off-chain estimate subject to sandwich/slippage), permanently loses the difference. The lost native token accumulates in `EvmHost` and can only be recovered by cross-chain governance sweeping it to an address of its choosing — not back to the payer. This is a direct fund-loss bug matching the accepted impact class ("stealing or loss of funds") triggered purely by normal usage of a public, unprivileged entry point (`dispatch`/`fundRequest`), with no malicious relayer, prover, or admin required.

### Likelihood Explanation
High. Overpaying `msg.value` relative to the exact fee-token amount is the expected, documented usage pattern (the docs explicitly instruct users to add slippage buffer since `getAmountsIn` estimates are approximate and subject to sandwich attacks/slippage). Every native-token dispatch or fundRequest call that overshoots the exact amount needed by the swap will trigger this loss, not just an edge case.

### Recommendation
Mirror the pattern already used in `IntentGatewayV2.sol`/`ExtrinsicIntents.sol`: capture the `amounts[0]` (actual ETH spent) returned by `swapETHForExactTokens`, compute `refund = msg.value - amounts[0]`, and forward it back to `_msgSender()` (or `post.payer`/`get`'s caller as appropriate) within `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`.

### Proof of Concept
1. User calls `EvmHost.dispatch{value: X}(post)` where `post.fee = F` (desired fee-token amount), and supplies `X` native token with a reasonable slippage buffer (e.g., quoted cost + 5%) as recommended by the protocol's own documentation. [1](#0-0) 
2. Internally, `swapETHForExactTokens{value: X}(F, path, address(this), block.timestamp)` spends only `Y <= X` ETH to acquire exactly `F` fee tokens for `EvmHost`, and refunds `X - Y` ETH — but since the router's caller is `EvmHost`, this refund goes to `EvmHost`, not the user.
3. `dispatch()` never checks or forwards this leftover ETH; it silently remains in `EvmHost`'s balance.
4. The user has irrecoverably lost `X - Y` native token; the only path to move it out is a governance-only `withdraw()` call to an arbitrary beneficiary, which does not return funds to the original user. [5](#0-4)

### Citations

**File:** evm/src/core/EvmHost.sol (L647-660)
```text
    /**
     * @dev withdraws host revenue to the given address, can only be called by cross-chain governance
     * @param params, the parameters for withdrawal
     */
    function withdraw(WithdrawParams memory params) external restrict(_hostParams.hostManager) {
        if (params.token == address(0)) {
            // this is safe because re-entrancy is mitigated before dispatching requests
            (bool sent,) = params.beneficiary.call{value: params.amount}("");
            if (!sent) revert WithdrawalFailed();
        } else {
            IERC20(params.token).safeTransfer(params.beneficiary, params.amount);
        }
        emit HostWithdrawal({beneficiary: params.beneficiary, amount: params.amount, token: params.token});
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
