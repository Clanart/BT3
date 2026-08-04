## Analog Found

### Title
Native-fee overpayment in `EvmHost.dispatch()`/`fundRequest()` is refunded to the Host itself instead of the depositor, permanently trapping user funds - (File: `evm/src/core/EvmHost.sol`)

### Summary
The external report's core broken invariant is: *code trusts an imprecise, swap-derived amount as if it were exact, and moves real value based on that estimate without a safe fallback path for the difference.* Hyperbridge's `EvmHost` reproduces this exact class of bug — not in a read-only quoter, but in the live `swapETHForExactTokens` call used for native-fee dispatch, where the leftover ETH from an imprecise `msg.value` is refunded to the wrong party.

### Finding Description
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` all execute the same pattern when a user pays with native token: [1](#0-0) 

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
    }
    ...
```

`swapETHForExactTokens` on the Uniswap V2 router only consumes exactly enough ETH to buy `post.fee` units of `feeToken()`; **any leftover `msg.value` is refunded by the router to `msg.sender` of the swap call**. Since `EvmHost` itself is the caller of the router (not the original user), that refund lands on `address(this)` — the Host contract — not on the depositing user. The identical pattern appears in `dispatch(DispatchGet)` and `fundRequest()`. [2](#0-1) [3](#0-2) 

This is the exact anti-pattern the report describes: relying on an approximate/pre-swap amount (here, the caller's off-chain slippage-buffered `msg.value`) for a real state-changing execution, without reconciling the true output against the depositor's intent. Indeed, the SDK explicitly instructs callers to overpay by design — a 1% buffer is added on top of every native-fee estimate before calling dispatch: [4](#0-3) [5](#0-4) 

Because `getAmountsIn`/router pricing is itself only an estimate subject to slippage between quote-time and execution-time (the docs even warn `quote()` "uses Uniswap's `getAmountsIn`, making it vulnerable to sandwich attacks" and should only be used off-chain), any caller following the SDK's own guidance — or simply padding `msg.value` for safety, or hitting favorable price movement between estimation and execution — will systematically send more native token than `dispatch` consumes. That surplus is not returned to them; it is swept into the Host's own balance with no code path in `EvmHost.sol` that credits, tracks, or lets the original payer reclaim it. Only privileged `hostManager`-governed withdrawals can move funds out of the Host, and those are not tied to per-user refund accounting.

### Impact Explanation
This causes an unprivileged, ordinary user (anyone dispatching a POST/GET request or funding one with native token) to permanently lose the delta between their supplied `msg.value` and the actual native cost of the swap, on every single call — not via a hostile actor, relayer, or prover, but purely as a side effect of normal usage recommended by the protocol's own SDK (1% buffer). This is a direct, repeatable loss-of-funds bug matching the bounty's "stealing or loss of funds" category, requiring no malicious peer, governance actor, or front-running condition — it fires deterministically whenever `msg.value` > exact swap input needed.

### Likelihood Explanation
High. Any client using the documented SDK flow (`quoteNative`, buffered fee estimates) or any user manually estimating fees off-chain (since `quote()`/`getAmountsIn` values drift between estimation and execution due to normal AMM price movement) will overpay by construction. There is no edge case required — it is the expected, common-case execution path for every native-fee dispatch.

### Recommendation
- After `swapETHForExactTokens`, compute the leftover balance (`msg.value - amounts[0]`, using the returned `amounts` array from the router) and refund it directly to `_msgSender()` in the same transaction, rather than letting the router's internal refund land on `address(this)`.
- Alternatively, call the router with `to = address(this)` for the swap and use `swapExactETHForTokens`-style logic that explicitly returns unused input to the original caller, verified against the router's return value.
- Add a test asserting that a user who sends `msg.value` greater than the exact ETH needed for `post.fee`/`get.fee` gets the surplus back in their own balance after `dispatch`/`fundRequest`.

### Proof of Concept
1. User calls `EvmHost.dispatch{value: X}(DispatchPost{..., fee: F})` where `X` is deliberately (or per SDK's 1% buffer) larger than the exact ETH needed to buy `F` feeTokens via `uniswapV2`.
2. Inside `dispatch`, `swapETHForExactTokens{value: X}(F, path, address(this), block.timestamp)` executes; the router consumes `amounts[0] < X` and refunds `X - amounts[0]` wei to `msg.sender`, which is `EvmHost` (the caller), not the original user.
3. `dispatch` returns successfully; the request commitment is created correctly, but the user's ETH surplus is now permanently held by `EvmHost` with no code path (`evm/src/core/EvmHost.sol`) that attributes or returns it to them.
4. Repeat with every dispatch call using native payment (as SDK’s `convertNativeToFeeToken`/`quoteNative` buffer patterns encourage) to accumulate protocol-captured, unrecoverable user funds. [1](#0-0)

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

**File:** evm/src/core/EvmHost.sol (L1031-1043)
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

**File:** sdk/packages/sdk/src/protocols/tokenGateway.ts (L176-181)
```typescript
			// Calculate gas cost in native tokens (gas * gasPrice)
			const gasCostInNative = gas * gasPrice

			// Add 1% buffer to relayer fee
			relayerFee = (gasCostInNative * 101n) / 100n
		}
```

**File:** sdk/packages/sdk/src/protocols/tokenGateway.ts (L207-212)
```typescript
		// Get protocol fee from source chain by calling quoteNative
		// This returns the cost in native tokens for dispatching the request
		const protocolFeeInNative = await this.source.quoteNative(postRequest, relayerFee)

		// Add 1% buffer to the protocol fee
		const protocolFeeWithBuffer = (protocolFeeInNative * 101n) / 100n
```
