Based on the investigation, the strongest local analog to the "fixed value causing locked funds" bug class is in `EvmHost.sol`'s native-token fee payment path.

### Title
Native ETH overpayment in `EvmHost` dispatch/fundRequest is refunded to the Host itself instead of the caller, permanently locking excess funds - (File: `evm/src/core/EvmHost.sol`)

### Summary
The original Beedle report is about a contract using a fixed/inflexible parameter (`fee: 3000`) when interacting with an external swap venue, which can leave value stranded in the contract because the swap doesn't match the real state of the pool it's routed against. The Hyperbridge analog is `EvmHost`'s native-payment path, which always performs an *exact-output* swap (`swapETHForExactTokens`) sized to the protocol-determined amount (`post.fee`/`get.fee`/`amount`), and lets the Uniswap V2 router refund any unspent `msg.value` — but the refund target is hardcoded to be `msg.sender` of the router call, which is `EvmHost`, not the original user who sent the overpayment.

### Finding Description
`dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest(...)` in `EvmHost.sol` each do: [1](#0-0) [2](#0-1) 

When `msg.value > 0`, `EvmHost` calls `IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(exactOutputAmount, path, address(this), block.timestamp)`. Uniswap V2's `swapETHForExactTokens` implementation only consumes as much ETH as needed to produce the exact output amount and refunds `msg.value - amounts[0]` back to `msg.sender` of that call — which, from the router's perspective, is `EvmHost`, not the externally-owned account (or app contract) that originally sent `msg.value` to `EvmHost.dispatch()`/`fundRequest()`.

This is structurally the same broken invariant as the reported bug: a fixed/hardcoded parameter (there: fee tier; here: exact-output amount plus router-refund semantics) is applied without regard to the actual value supplied, and any surplus ends up stuck in the contract rather than returned to the party who is entitled to it. `EvmHost` provides no mechanism visible in this path (or in the grepped `withdraw`-style functions in the file) that returns this residual native ETH to the original depositor — funds sent in excess of the exact swap requirement are absorbed into the host contract's ETH balance with no per-user accounting.

### Impact Explanation
Any user or app calling `dispatch()`/`fundRequest()` with native token payment and providing `msg.value` even slightly above what the exact-output swap consumes will have the difference permanently locked in `EvmHost`, unrecoverable by that depositor. Given `quote()`/`quoteNative()` are documented as off-chain estimates subject to slippage between estimation and execution (sandwich-attack warning in the docs), some overpayment is a normal/expected condition for any caller who doesn't submit the exact wei amount computed at the moment of execution — meaning this is not an edge case but a routine path that leaks value out of user control into the contract. [3](#0-2) 

### Likelihood Explanation
High likelihood of triggering: this fires on every native-token dispatch/fundRequest call where the caller supplies `msg.value` greater than the exact wei needed by the router at execution time (common due to price movement between quote and execution, or callers intentionally over-sending for safety margin). No special privilege, malicious relayer, or governance action is required — an ordinary unprivileged caller loses funds simply by using the documented native-payment flow.

### Recommendation
After the `swapETHForExactTokens` call, capture `EvmHost`'s ETH balance delta (or read the router's returned `amounts[0]`) and explicitly forward any leftover `msg.value` back to `_msgSender()` (or the designated `payer`), mirroring the refund pattern seen elsewhere in the codebase (e.g., `UniV4UniswapV2Wrapper.swapETHForExactTokens` explicitly forwards `refundETH` back to `msg.sender` of the *original* call rather than leaving it with the wrapper): [4](#0-3) 

Apply the same balance-delta-and-refund pattern to `EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest`.

### Proof of Concept
1. Caller estimates required native amount off-chain via `quoteNative()` and sends a transaction to `EvmHost.dispatch(post)` with `msg.value = X`.
2. Between estimation and inclusion, the pool price moves favorably (or the caller pads `msg.value` for safety), so the router needs only `X' < X` wei to produce `post.fee` in `feeToken`.
3. `swapETHForExactTokens{value: X}(post.fee, path, address(this), block.timestamp)` executes, consuming `X'` and refunding `X - X'` to `msg.sender` of the call, i.e., `EvmHost`.
4. The refunded ETH sits in `EvmHost`'s balance; the original caller has no function in the `dispatch`/`fundRequest` flow that returns it to them, and it is unaccounted for in `_requestCommitments`/`FeeMetadata`.

Note: I was not able to fully inspect the 4 `withdraw`-matching locations found in `EvmHost.sol` within the available iterations (likely feeToken-only admin withdrawal via `HostManager`), so I cannot rule out with full certainty that an admin-only sweep function exists for this stray ETH — if it does, the issue is downgraded from "fund lock" to "admin-recoverable, but not user-recoverable" fund diversion. I'd recommend verifying `HostManager`'s withdraw function signature in `evm/src/core/EvmHost.sol` and `evm/src/modules/HostManager.sol` (or equivalent) to confirm whether it can move native ETH (versus only `feeToken`).

### Citations

**File:** evm/src/core/EvmHost.sol (L908-932)
```text
    /**
     * @dev Dispatch a POST request to Hyperbridge
     *
     * @notice Payment for the request can be made with either the native token or the feeToken.
     * If native tokens are supplied, it will perform a swap under the hood using the local uniswap router.
     * Will revert if enough native tokens are not provided.
     *
     * If no native tokens are provided then it will try to collect payment from the calling contract in
     * the feeToken.
     *
     * @param post - post request
     * @return commitment - the request commitment
     */
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

**File:** evm/src/utils/uniswapv2/UniV4UniswapV2Wrapper.sol (L83-96)
```text
        // Snapshot standing balance (excluding inbound msg.value) so the refund is the swap-call delta only,
        // immune to any ETH that lands on the wrapper from outside the router (e.g., selfdestruct, coinbase).
        uint256 balanceBefore = address(this).balance - msg.value;

        IUniversalRouter(_params.universalRouter).execute{value: msg.value}(
            abi.encodePacked(bytes1(uint8(Commands.V4_SWAP))), inputs, deadline
        );

        uint256 refundETH = address(this).balance - balanceBefore;

        if (refundETH > 0) {
            (bool success,) = msg.sender.call{value: refundETH}("");
            require(success, "ETH refund failed");
        }
```
