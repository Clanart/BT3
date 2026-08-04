## Title
Excess native `msg.value` sent to `EvmHost.dispatch()`/`fundRequest()` is silently absorbed by the Uniswap swap and never refunded to the caller - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` all accept native token payment for relayer fees and swap the entire `msg.value` through `swapETHForExactTokens{value: msg.value}(...)`. Unlike the sibling code path in `IntentGatewayV2.placeOrder` (and `IntrinsicIntents`/`ExtrinsicIntents`), which captures the router's returned `amounts[0]` (actual ETH spent) and refunds the difference to `msg.sender`, `EvmHost`'s three functions discard the return value entirely and never refund unspent native tokens.

### Finding Description
In `EvmHost.sol`: [1](#0-0) [2](#0-1) [3](#0-2) 

Each of these functions does:
```solidity
IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
    post.fee, path, address(this), block.timestamp
);
```
`swapETHForExactTokens` is a Uniswap V2 exact-output swap: it consumes only as much ETH as required to obtain `post.fee` output tokens, and refunds the unused ETH — but that refund goes back to `msg.sender` **of the router call**, which is `EvmHost` itself, not the original end-user who invoked `dispatch()`/`fundRequest()`. The refunded ETH lands in `EvmHost`'s own balance. `EvmHost` never reads `amounts[0]` and never forwards any leftover native value back to `_msgSender()`.

This is exactly the bug-class in the referenced report: the check only ensures "enough" `msg.value` was supplied (implicitly, via the swap's `amountInMaximum = msg.value` semantics), but any excess supplied by the user is not tracked or returned.

Contrast with the correct pattern already implemented elsewhere in the same repository, `IntentGatewayV2.placeOrder`, which explicitly captures the swap's actual spend and refunds the remainder to the caller: [4](#0-3) 

The `EvmHost` dispatch functions were not updated to follow this same accounting/refund pattern, even though they are the primary, most-used entry points for dispatching cross-chain POST/GET requests and funding pending requests with native currency.

### Impact Explanation
Any unprivileged user who calls `IDispatcher(host).dispatch{value: X}(post)` (directly, or via `HyperApp`-based apps such as `HyperFungibleToken.send`, `HyperbridgeLzEndpoint.send`, or via the documented pattern of sending `msg.value` for native fee payment) will lose any ETH sent beyond what the Uniswap swap actually consumes to produce `post.fee` (or `get.fee`, or `amount` for `fundRequest`) fee tokens. Because slippage/market conditions make it impossible for callers to send the *exact* required native amount, the documented usage pattern (`dispatch{value: msg.value}(post)`) systematically causes value loss on every call, with the surplus permanently retained in the `EvmHost` contract rather than the depositor. This is a direct loss-of-funds bug reachable by any ordinary user through a completely unprivileged, core public entry point of the bridge (no relayer, prover, or admin needed).

### Likelihood Explanation
High. This path is exercised on every native-token-funded `dispatch()`/`fundRequest()` call, which is the officially documented way to pay Hyperbridge dispatch fees in native currency (see `docs/content/developers/evm/messaging/post-requests.mdx` and `get-requests.mdx`, which explicitly instruct callers to send `msg.value` and let "the Host handle the Uniswap swap"). Since users cannot know the exact swap execution price in advance, they will routinely over-supply `msg.value` as a safety margin, and that margin is lost every time. The repository's own IntentGateway code demonstrates the team is aware refunding is necessary (it implements the correct pattern there), confirming this is an inconsistency/oversight in `EvmHost` rather than an intentional design choice.

### Recommendation
In `EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()`, capture the `amounts` array returned by `swapETHForExactTokens` and refund any unspent native value (`msg.value - amounts[0]`) back to `_msgSender()`, mirroring the pattern already used in `IntentGatewayV2.placeOrder`:
```solidity
uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
    post.fee, path, address(this), block.timestamp
);
uint256 refund = msg.value - amounts[0];
if (refund > 0) {
    (bool sent,) = _msgSender().call{value: refund}("");
    if (!sent) revert RefundFailed();
}
```

### Proof of Concept
1. User calls `EvmHost.dispatch{value: 5 ether}(post)` where `post.fee = 10 USDC`-equivalent in fee tokens, expecting the router to consume roughly 1 ETH worth and refund the rest.
2. `swapETHForExactTokens{value: 5 ether}(post.fee, [WETH, feeToken], address(this), block.timestamp)` executes; the Uniswap router internally computes it only needs ~1 ETH, executes the swap, and refunds ~4 ETH — but to `msg.sender` of that call, i.e., `EvmHost`.
3. `EvmHost` never reads the returned `amounts[0]`, and its `dispatch()` function has no refund step after the swap call.
4. The ~4 ETH surplus becomes part of `EvmHost`'s contract balance permanently; the user who sent 5 ether receives nothing back and has no way to reclaim the difference through any public interface.
5. This can be validated against the existing test suite pattern used for `IntentGatewayV2` (`testPlaceOrder_RefundsExcessNativeToken`, `testPlaceOrder_FeeSwap_RefundsExcessNativeToken` in `evm/tests/foundry/IntentGatewayV2*.sol`), which explicitly assert refund behavior for the equivalent code path — no equivalent test exists for `EvmHost.dispatch`/`fundRequest`, and manually tracing the code confirms no refund occurs there.

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
