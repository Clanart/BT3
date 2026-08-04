## Analog Identified: Excess native ETH sent to `EvmHost.dispatch()`/`fundRequest()` is never returned to the original payer — it is silently absorbed by the Host contract

### Title
Native-token overpayment on dispatch is permanently lost when callers forward `msg.value` to `EvmHost.dispatch()`/`fundRequest()` instead of computing the exact swap cost themselves - (File: `evm/src/core/EvmHost.sol`)

### Summary
The M-08 report's core broken invariant is: a payment-forwarding function is assumed to "refund" or "consume exactly what's needed," but the actual consumer only tracks/returns a partial amount, so the caller's leftover value gets trapped instead of returned to the payer. The same broken invariant exists in Hyperbridge's `EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`: when paid in native token, they forward the **entire `msg.value`** into `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(post.fee, ...)` and never account for, or forward back, the unused remainder.

### Finding Description
`EvmHost.dispatch(DispatchPost memory post)` does: [1](#0-0) 

`swapETHForExactTokens` is a standard Uniswap V2 router function whose dust-refund logic is `TransferHelper.safeTransferETH(msg.sender, msg.value - amounts[0])`. Because `EvmHost` itself is the direct caller of the router, `msg.sender` inside the router call is the `EvmHost` contract — **not** the original app/user who sent the native value to `dispatch()`. The result: any overpayment beyond the exact swap cost of `post.fee` lands back in `EvmHost`'s own balance, and `EvmHost.dispatch()` has no logic afterward to forward that residual back to `post.payer`/`_msgSender()`.

Contrast this with `IntentGatewayV2`/`ExtrinsicIntents`, which do the swap **themselves**, capture the `amounts[0]` actually spent, and explicitly refund the difference to the caller: [2](#0-1) [3](#0-2) 

But several other call sites route native payment straight through to the Host without this accounting, relying on the false assumption that "excess is refunded by the router":

- `ExtrinsicIntents._cancelFromSource` forwards the whole `msg.value` to `dispatch(DispatchGet)` with no post-call refund: [4](#0-3) 

- `HyperFungibleToken.send()` forwards `msg.value` directly: [5](#0-4) 

- `HyperbridgeLzEndpoint.send()` explicitly relies on this incorrect assumption, and its `quote()` deliberately **doubles** the estimated native fee, with a comment stating the excess "is refunded by the uniswap router": [6](#0-5) [7](#0-6) 

That comment is incorrect: the router's refund terminates at `EvmHost`, not at `HyperbridgeLzEndpoint` or its calling user, because the router's `msg.sender` in that internal call is `EvmHost`. Since the endpoint's `quote()` intentionally instructs callers to send **2x** the real native fee "to absorb legacy per-byte fee markup," every dispatch through this path permanently strands roughly half of every fee payment inside `EvmHost`'s balance — with no path back to the payer.

The same unaccounted pass-through pattern applies to `fundRequest()`: [8](#0-7) 

### Impact Explanation
This is a direct, protocol-architecture-level loss-of-funds bug (not a griefing/relayer/prover assumption): any user or integrating contract that pays dispatch fees in native token through `EvmHost.dispatch()`/`fundRequest()` via a pass-through pattern (as `HyperbridgeLzEndpoint`, `HyperFungibleToken`, `WrappedHyperFungibleTokenUpgradeable`, and `ExtrinsicIntents._cancelFromSource` all do) loses any amount sent beyond the exact fee-token cost of the swap. This is systemic: the `HyperbridgeLzEndpoint.quote()` function guarantees an overpayment of ~2x on every single native-fee dispatch by design, meaning normal, honest usage of this documented, intended path permanently forfeits roughly 50% of the native value sent on every cross-chain message, with the funds accumulating unrecoverably in `EvmHost`'s balance rather than being returned to the paying user/application.

### Likelihood Explanation
High likelihood: this is not a rare edge case — it is the expected/normal execution path for `HyperbridgeLzEndpoint.send()` given its `quote()` intentionally returns 2x the real fee, and it applies to any other integrator that follows the documented pattern in `docs/content/developers/evm/messaging/post-requests.mdx` ("dispatch directly and let the Host handle the Uniswap swap") without independently tracking and refunding the swap's actual cost. No malicious relayer, prover, or governance actor is required — it triggers purely from normal use of public, unprivileged entry points (`send`, `dispatch`, `fundRequest`, `cancel`).

### Recommendation
Modify `EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` to capture the `amounts[0]` actually spent by `swapETHForExactTokens` and refund `msg.value - amounts[0]` to `_msgSender()` (or `post.payer`) before returning, mirroring the pattern already implemented correctly in `IntentGatewayV2`/`ExtrinsicIntents`. Additionally, update `HyperbridgeLzEndpoint.quote()` to stop deliberately doubling the native fee once the Host properly refunds excess, or keep the buffer only if the refund path is fixed.

### Proof of Concept
1. A user calls `HyperbridgeLzEndpoint.send()`/`quote()`-derived flow (or any `HyperFungibleToken.send()` / `ExtrinsicIntents.cancel`) supplying `msg.value` equal to the quoted (buffered) native fee.
2. `IDispatcher(_host).dispatch{value: msg.value}(request)` is invoked (`HyperbridgeLzEndpoint.sol:296-297`).
3. Inside `EvmHost.dispatch()`, `swapETHForExactTokens{value: msg.value}(post.fee, ...)` executes; the router refunds `msg.value - amounts[0]` to `msg.sender`, which resolves to `address(EvmHost)` (`EvmHost.sol:921-932`).
4. `EvmHost.dispatch()` returns without forwarding any refund to the caller.
5. Result: the difference (up to ~50% of the sent value, per the endpoint's own 2x buffering logic) remains stuck in `EvmHost`'s native balance, unrecoverable by the original payer through any public function.

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L206-220)
```text
        keys[0] = bytes.concat(abi.encodePacked(_instance(order.destination)), _calculateCommitmentSlotHash(commitment));
        DispatchGet memory request = DispatchGet({
            dest: order.destination,
            keys: keys,
            timeout: 0,
            height: options.height,
            fee: options.relayerFee,
            context: context,
            payer: msg.sender
        });

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2128-2163)
```text
    /// @notice Excess msg.value beyond native input legs is refunded to the user.
    function testPlaceOrder_RefundsExcessNativeToken() public {
        uint256 inputAmount = 1 ether;
        uint256 overpayment = 0.5 ether;

        TokenInfo[] memory inputs = new TokenInfo[](1);
        inputs[0] = TokenInfo({token: bytes32(0), amount: inputAmount}); // native ETH

        TokenInfo[] memory outputAssets = new TokenInfo[](1);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1000 * 1e6});

        PaymentInfo memory output =
            PaymentInfo({beneficiary: bytes32(uint256(uint160(user))), assets: outputAssets, call: ""});

        Order memory order = Order({
            user: bytes32(0),
            source: "",
            destination: host.host(),
            deadline: block.number + 100,
            nonce: 0,
            fees: 0,
            session: address(0),
            predispatch: DispatchInfo({assets: new TokenInfo[](0), call: ""}),
            inputs: inputs,
            output: output
        });

        uint256 userBalBefore = user.balance;

        vm.prank(user);
        intentGateway.placeOrder{value: inputAmount + overpayment}(order, bytes32(0));

        // User should only have spent inputAmount, overpayment refunded
        assertEq(user.balance, userBalBefore - inputAmount, "Overpayment should be refunded");
        assertEq(address(intentGateway).balance, inputAmount, "Gateway should only hold escrowed amount");
    }
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L264-270)
```text
    function send(SendParams calldata params) external payable whenNotPaused {
        _burn(msg.sender, params.amount);
        DispatchPost memory request = _buildDispatchPost(params);

        bytes32 commitment;
        if (msg.value > 0) {
            commitment = IDispatcher(_host).dispatch{value: msg.value}(request);
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L296-298)
```text
        if (msg.value > 0) {
            IDispatcher(_host).dispatch{value: msg.value}(request);
        } else {
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L337-345)
```text
        // Apply a generous 2x buffer to absorb the legacy deployed host's
        // per-byte protocol fee (the in-source host has no such markup). Excess
        // native is refunded by the uniswap router; excess feeToken approval is
        // simply unused.
        if (_params.payInLzToken) {
            return MessagingFee({nativeFee: 0, lzTokenFee: request.fee * 2});
        } else {
            return MessagingFee({nativeFee: quote(request) * 2, lzTokenFee: 0});
        }
```
