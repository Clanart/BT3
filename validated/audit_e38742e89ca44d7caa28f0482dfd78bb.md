### Title
Unrefunded native-token overpayment permanently locked in `EvmHost` on fee-token swap - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` accept native token payment and swap it for the exact protocol fee via Uniswap V2's `swapETHForExactTokens`. This is an exact-*output* swap: the caller must send `msg.value` that is *at least* the amount needed at current pool price, and the router refunds any unspent ETH to whoever called it — which is `EvmHost` itself, not the original `_msgSender()`. None of these three functions forward that refund back to the caller, so any native token sent above the exact price-dependent requirement is permanently stranded in `EvmHost`.

### Finding Description
In `evm/src/core/EvmHost.sol`, all three fee-paying entry points follow the same pattern: [1](#0-0) 

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
    } else if (post.fee > 0) {
        IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), post.fee);
    }
    ...
```

`swapETHForExactTokens(amountOut, path, to, deadline)` on Uniswap V2 spends only as much ETH as the current price requires to deliver exactly `amountOut` fee tokens, and refunds the leftover ETH to `msg.sender` of the router call. Here the router's caller is `EvmHost` itself (`address(this)` inside `dispatch`/`fundRequest`), so the leftover lands in `EvmHost`'s own balance — not back to `_msgSender()` who originally paid.

The identical pattern recurs in `dispatch(DispatchGet)` and `fundRequest`: [2](#0-1) [3](#0-2) 

None of these three functions contains any subsequent step that measures the actual ETH consumed and returns the difference to `_msgSender()`. Since the exact ETH needed to buy `post.fee`/`get.fee`/`amount` worth of fee token depends on the live Uniswap pool price at execution time, callers cannot know it precisely in advance and must send `msg.value` with some safety margin to avoid a revert (`INSUFFICIENT_OUTPUT_AMOUNT`/reverting on price movement) — guaranteeing that overpayment is a routine occurrence, not an edge case. Once the router refunds, the surplus is trapped in `EvmHost` with no observed sweep or withdrawal endpoint in this contract to return it to the original payer.

This is the same broken invariant as the report's slippage-protection bug class — a swap executed without safely handling the resulting difference between requested and actual amounts — but the local analog manifests as unrecoverable loss of the overpaying caller's native funds inside the bridge's core host contract, rather than a front-run price attack.

Callers of `dispatch()`/`fundRequest()` are not limited to end users manually: cross-chain apps built on top of `EvmHost`, e.g. `IntentGatewayV2`/`ExtrinsicIntents.sol`, forward `msg.value` into `IDispatcher(hostAddr).dispatch{value: ...}(request)` when paying relayer fees in native token, meaning intent-fill flows regularly route through this exact-output swap and can lose the overpaid margin on every single dispatch.

### Impact Explanation
Any unprivileged account that pays the Hyperbridge dispatch/fund-request fee in native token loses the portion of `msg.value` beyond what the pool needed at execution time — an ordinary, expected occurrence given price volatility and the impossibility of predicting the exact swap rate ahead of the transaction. This is a direct, permanent loss of user funds with no path to recovery, matching the bounty's "stealing or loss of funds" impact category. Because `dispatch`/`fundRequest` are core, high-traffic entry points (also used internally by `IntentGatewayV2`/`ExtrinsicIntents` fee-in-native flows), the leakage compounds across nearly every native-fee-paying transaction on the protocol.

### Likelihood Explanation
High. Any caller providing native token for dispatch/fund-request fees is affected on essentially every call, since exact-output AMM pricing changes block-to-block and callers must overshoot `msg.value` to avoid reverts. No malicious relayer, prover, governance actor, or front-running is required — this is triggered by ordinary correct usage of the public, permissionless `dispatch`/`fundRequest` API.

### Recommendation
After calling `swapETHForExactTokens`, compute the actual ETH spent (e.g., from the returned `amounts[0]` array) and refund the difference (`msg.value - amounts[0]`) directly to `_msgSender()` in `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest`, mirroring the refund pattern already used correctly elsewhere in the codebase (e.g., `ExtrinsicIntents.sol`'s final `msg.sender.call{value: msgValue}("")` for its own locally-tracked leftover).

### Proof of Concept
1. Caller invokes `EvmHost.dispatch{value: X}(post)` where `post.fee = F` (fee-token units) and `X` is set generously above the current quoted ETH cost of `F` fee tokens (as any rational caller must do to tolerate price movement between quote and execution).
2. Inside `dispatch`, `swapETHForExactTokens{value: X}(F, [WETH, feeToken], address(this), deadline)` executes, spending only `Y < X` ETH (the exact amount needed at execution-time price) and refunding `X - Y` ETH to `msg.sender` of the swap call, i.e., `EvmHost`.
3. `dispatch` returns normally, having received exactly `F` fee tokens and consumed `Y` ETH from the user's `X`.
4. The leftover `X - Y` ETH now sits in `EvmHost`'s balance. No line in `dispatch` (or `fundRequest`/`dispatch(DispatchGet)`) transfers this back to `_msgSender()`.
5. Repeat across calls (and via `IntentGatewayV2`/`ExtrinsicIntents` forwarding native fee payments into `EvmHost.dispatch`); the trapped ETH accumulates in `EvmHost` with no code path shown that returns it to the payers who overpaid.

Note: I was unable to fully verify within the available index whether `EvmHost.sol` (or an inherited base contract not captured by search) contains an admin/governance sweep function for the contract's stray native balance; if such a function exists, it would still constitute a loss for the original payer (funds recovered only by governance, not by the affected user), which still satisfies "loss of funds" under the impact gate, but does not eliminate the missing per-call refund as the root cause.

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
