### Title
Excess native ETH sent to `EvmHost.dispatch()`/`fundRequest()` is swap-refunded to the host contract, not the caller, and is permanently lost - (File: evm/src/core/EvmHost.sol)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` all accept native `msg.value` and internally swap it into `feeToken` via `IUniswapV2Router02.swapETHForExactTokens`. Standard `UniswapV2Router02.swapETHForExactTokens` refunds any unused ETH to its immediate caller — which is `EvmHost` itself, not the external account that originally sent `msg.value`. Unlike the Aave `repay()` bug, this is not a misdirected refund to a third-party beneficiary, but a complete loss path: `EvmHost` never forwards the leftover ETH back to `_msgSender()`, so any overestimate of the ETH quote required for the fee-token swap is permanently stranded in the host contract with no way for the payer to recover it.

### Finding Description
In `EvmHost.sol`, the native-payment branch of `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` is: [1](#0-0) [2](#0-1) [3](#0-2) 

Each of these calls `IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(fee, path, address(this), block.timestamp)` and then proceeds immediately — there is no snapshot of `address(this).balance` before/after the swap, and no forwarding of the swap's ETH refund to `_msgSender()`.

The canonical `UniswapV2Router02.swapETHForExactTokens` implementation refunds any `msg.value` in excess of the amount actually spent to `msg.sender` — but in this call chain, `msg.sender` of the router call is `EvmHost`, not the original account that called `dispatch()`/`fundRequest()`. The refunded ETH therefore lands in `EvmHost`'s own balance and is never routed back out.

This is exactly the broken invariant from the seed report: excess payment must be returned to the actual payer, and it is not. Here it is worse than the Aave case (misdirection to the wrong-but-still-legitimate beneficiary) — the funds go to neither the payer nor any legitimate on-chain beneficiary; they become inert, locked value with no documented sweep/rescue mechanism visible in `EvmHost.sol`.

By contrast, the `IntentGatewayV2`/`ExtrinsicIntents.sol`/`IntrinsicIntents.sol` contracts in this same repo correctly implement this exact refund pattern by tracking `msgValue` locally and explicitly returning unspent native tokens to `msg.sender` at the end of the call: [4](#0-3) [5](#0-4) 

and the custom `UniV4UniswapV2Wrapper.sol` even documents the need for exactly this pattern: [6](#0-5) 

This confirms the pattern is a known, previously-fixed class of bug elsewhere in the codebase, but `EvmHost.dispatch()`/`fundRequest()` — the core, most widely used entrypoints for every HyperApp integrator sending POST/GET requests — lack it.

### Impact Explanation
Any unprivileged caller (an EOA or any `HyperApp` contract) that dispatches a POST/GET request or funds a pending request with native token payment and slightly overestimates the ETH required for the AMM swap into `feeToken` will have the excess permanently and unrecoverably absorbed into `EvmHost`'s balance. This is a direct, protocol-native loss-of-funds bug reachable by any normal user through the primary fee-payment path documented for the entire SDK/dApp ecosystem (`docs/content/developers/evm/messaging/post-requests.mdx` explicitly documents native-token payment as a first-class option). Given that exact ETH quoting against a live AMM price is inherently imprecise (slippage between quote-time and execution-time), overpayment is the common case, not an edge case, making this loss systematic rather than theoretical.

### Likelihood Explanation
High. `dispatch()` with native `msg.value` and `fundRequest()` with native `msg.value` are documented, first-class payment paths for third-party HyperApps and end users; the docs explicitly state: "Native token (ETH, BNB, POL, DOT etc.) — Sent with transaction via msg.value" as a supported method. Any caller who does not send the exact router-quoted amount (which is nearly guaranteed given price-impact/slippage between quote and execution) will lose the difference on every single call. No special privileges, malicious actors, or unusual conditions are required.

### Recommendation
In `EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`, snapshot `address(this).balance` before invoking `swapETHForExactTokens`, and after the swap, compute the delta and forward any leftover native ETH back to `_msgSender()` via a low-level call, mirroring the pattern already used in `ExtrinsicIntents.sol`/`IntrinsicIntents.sol` and `UniV4UniswapV2Wrapper.sol`.

### Proof of Concept
1. Call `EvmHost.dispatch{value: X}(DispatchPost{fee: F, ...})` where `X` intentionally exceeds the router-quoted `amountsIn` needed to obtain `F` units of `feeToken` (e.g., quote via `getAmountsIn` and add 20% buffer to account for expected slippage, as any integrator reasonably would to avoid revert-on-underpayment).
2. `IUniswapV2Router02.swapETHForExactTokens{value: X}(F, path, address(this), deadline)` executes, spending only `amountsIn <= X` and refunding `X - amountsIn` ETH to its caller, `EvmHost`.
3. Observe `address(host).balance` increases by `X - amountsIn` after the call, while the caller's balance decreases by the full `X`; the caller never receives the difference back — see `EvmHostForkTest.sol`'s `testCanDispatchPostRequestWithNative`/`testCanDispatchFundRequestWithNative`, which use an exact `quote()` helper and never test or assert on any refund of overpayment.
4. Compare against `IntentGatewayV2SameChainTest.sol::testPlaceOrder_RefundsExcessNativeToken` and `testFillOrder_RefundsSolverExcessNativeToken`, which explicitly assert overpayment IS refunded in the IntentGateway apps — confirming the absence of equivalent assertions/logic for `EvmHost.dispatch`/`fundRequest` is a real gap, not merely an untested feature. [7](#0-6) [8](#0-7)

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L164-168)
```text
        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L144-148)
```text
        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
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

**File:** evm/tests/foundry/EvmHostForkTest.sol (L36-103)
```text
    function testCanDispatchPostRequestWithNative() public {
        uint256 relayerFee = 10 * 1e18;

        // dispatch request
        bytes32 commitment = host.dispatch{value: quote(relayerFee)}(
            DispatchPost({
                body: abi.encodePacked(bytes32(0)),
                payer: whaleAccount,
                fee: relayerFee,
                dest: StateMachine.evm(421614),
                timeout: 0,
                to: abi.encode(bytes32(0))
            })
        );
        assert(host.requestCommitments(commitment).sender == whaleAccount);
    }

    function testCanDispatchGetRequestWithNative() public {
        uint256 relayerFee = 10 * 1e18;

        bytes[] memory keys = new bytes[](1);
        keys[0] = abi.encode(whaleAccount);

        // dispatch request
        uint256 cost = quote(relayerFee);
        vm.prank(whaleAccount);
        bytes32 commitment = host.dispatch{value: cost}(
            DispatchGet({
                dest: StateMachine.evm(97),
                height: 100,
                keys: keys,
                timeout: 60 * 60,
                context: new bytes(0),
                fee: relayerFee,
                payer: whaleAccount
            })
        );
        assert(host.requestCommitments(commitment).sender == whaleAccount);
    }

    function testCanDispatchFundRequestWithNative() public {
        // dispatch request
        vm.prank(whaleAccount);
        bytes32 commitment = host.dispatch(
            DispatchPost({
                body: abi.encode(bytes32(0)),
                payer: whaleAccount,
                fee: 0,
                dest: StateMachine.evm(421614),
                timeout: 0,
                to: abi.encode(bytes32(0))
            })
        );
        assert(host.requestCommitments(commitment).sender == whaleAccount);
        assert(host.requestCommitments(commitment).fee == 0);

        // fund request
        vm.prank(whaleAccount);
        uint256 newfee = 10 * 1e18;
        host.fundRequest{value: quote(newfee)}(commitment, newfee);
        assert(host.requestCommitments(commitment).fee == newfee);

        // can't fund unknown requests
        uint256 cost = quote(newfee);
        vm.expectRevert(EvmHost.UnknownRequest.selector);
        vm.prank(whaleAccount);
        host.fundRequest{value: cost}(keccak256(hex"dead"), 10 * 1e18);
    }
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2129-2163)
```text
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
