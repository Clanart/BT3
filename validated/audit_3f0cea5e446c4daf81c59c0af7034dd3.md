## Analysis

Confirmed: `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest` in `EvmHost.sol` all perform `swapETHForExactTokens{value: msg.value}(fee, path, address(this), ...)`, passing the **entire `msg.value`** as the max input instead of quoting/capping it to the exact amount needed for `fee`. Uniswap V2's `swapETHForExactTokens` only consumes what's needed and refunds the unused ETH — but it refunds it to `msg.sender` of the swap call, which is `EvmHost` itself, not the original caller who sent `msg.value` to `dispatch`/`fundRequest`. Unlike `IntentGatewayV2.placeOrder`, which explicitly tracks `msgValue` and refunds any unspent amount back to `msg.sender` at the end ( [1](#0-0) ), `EvmHost` has no such refund step after any of its three `swapETHForExactTokens` call sites. [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Unrefunded native-token overpayment permanently locked in `EvmHost` on `dispatch`/`fundRequest` fee swaps - (File: evm/src/core/EvmHost.sol)

### Summary
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest` swap the caller's entire `msg.value` via `swapETHForExactTokens`, requesting exactly `post.fee`/`get.fee`/`amount` tokens out. Uniswap V2 only pulls the wei actually needed and refunds the remainder to the swap caller (`EvmHost`), but `EvmHost` never forwards that refund back to the user. Any ETH sent above the exact fee requirement is stranded in the host contract with no accounting or sweep path tied to the original sender.

### Finding Description
Each of the three functions computes the swap the same way: `IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(fee, path, address(this), block.timestamp)`. The router uses `msg.value` only as the ceiling and refunds unused ETH via a low-level `call` to whoever invoked the swap — here, `EvmHost`. Because `receive()`/fallback accepts it silently and no code path tracks "unspent msgValue" the way `IntentGatewayV2` does, this ETH becomes indistinguishable protocol-owned balance. There is no user-facing withdrawal, quote-then-cap pattern, or refund transfer back to `_msgSender()` anywhere in these three functions.

### Impact Explanation
This is a direct fund-loss bug for any user or integrating contract that doesn't compute the exact wei needed off-chain and instead sends a safety margin (a very common practice, and one the report explicitly warns against for the analogous Stargate bug). The excess is not merely "refunded to the wrong beneficiary" via the router — it is refunded to the host contract with no bookkeeping that would let governance identify or return it to depositors, effectively a silent, permanent loss of user funds for every overpayment on `dispatch`/`fundRequest`.

### Likelihood Explanation
High — any caller who sends `msg.value` even slightly above the exact quoted price for `fee` tokens (unavoidable in practice due to price movement between quote and execution, or a caller intentionally padding as encouraged in other parts of this same codebase, e.g. `IntentGatewayV2`) will lose the difference. This requires no privileged actor, malicious relayer, or governance action — it triggers on ordinary, correct usage of the public `dispatch`/`fundRequest` entry points.

### Recommendation
Mirror the pattern already used in `IntentGatewayV2.placeOrder`: track `uint256 msgValue = msg.value` before the swap, subtract `amounts[0]` (actual ETH spent, returned by `swapETHForExactTokens`), and refund the remainder to `_msgSender()` with a checked low-level call, reverting on failure. Apply this fix identically to `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest`.

### Proof of Concept
1. Attacker/user calls `EvmHost.dispatch(DispatchPost({..., fee: 100e6, ...}))` sending `msg.value = 1 ether` (intending headroom against price slippage, or simply overestimating).
2. `swapETHForExactTokens{value: 1 ether}(100e6, path, address(this), deadline)` executes; suppose only `0.01 ether` is required to obtain `100e6` fee tokens.
3. Uniswap V2 refunds `0.99 ether` to `msg.sender` of the swap, i.e., `EvmHost`.
4. `dispatch` returns; the `0.99 ether` remains in `EvmHost`'s balance permanently, with no mapping, event, or later withdrawal path crediting it to the original caller.
5. Repeat across many callers — funds accumulate unrecoverably in the host, verifiable by comparing `address(host).balance` growth against emitted `fee` values in `PostRequestEvent`/`GetRequestEvent`, which never account for the swapped-and-refunded native surplus.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L364-368)
```text
        // Refund any unspent native tokens to the user.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
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
