## Analysis

**Core broken invariant (from H-01):** a swap embedded inside a public, unprivileged entrypoint uses an unconstrained execution bound (no real price-based min-out/max-in) and the caller has no way to bound their loss, so an MEV searcher can sandwich the swap and extract value that should have stayed with the caller.

**Local analog found:** `EvmHost.sol`'s `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` all perform a native→fee-token swap via `swapETHForExactTokens{value: msg.value}(...)` when the caller pays with native token, and none of them refund unused ETH back to the caller after the swap. [1](#0-0) [2](#0-1) [3](#0-2) 

By contrast, the newer `IntentGatewayV2` explicitly caps the amount spent and refunds the caller's unspent native token after the equivalent swap: [4](#0-3) 
This is confirmed by tests such as `testPlaceOrder_FeeSwap_RefundsExcessNativeToken`, which explicitly verifies "the user should get back most of the 5 ETH" after the fee swap. [5](#0-4) 

`EvmHost.dispatch`/`fundRequest` have no equivalent refund step — the amount left over from `swapETHForExactTokens` (which Uniswap sends back to `msg.sender`, i.e., `EvmHost` itself, since `address(this)` is passed as the swap `to`/router caller) is silently absorbed into the host contract with no path back to the payer.

### Title
Missing Refund and Unbounded `amountInMax` in `EvmHost` Native-Token Fee Swaps Enable Sandwich Value Extraction and Permanent Fund Loss - (evm/src/core/EvmHost.sol)

### Summary
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` swap native token to the fee token via `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(post.fee, path, address(this), block.timestamp)`. The entire `msg.value` is passed as the implicit `amountInMax`, with no cap derived from a fair/oracle price, and — unlike the equivalent logic in `IntentGatewayV2.placeOrder`/`fillOrder` — there is no step afterward to return unspent native token to the caller.

### Finding Description
Any caller dispatching a `PostRequest`/`GetRequest` or funding one with native token has no way to bound how much of their `msg.value` the swap will actually consume, other than sending exactly the amount they expect to spend. Uniswap V2's `swapETHForExactTokens` normally refunds unused input ETH to `msg.sender` of the router call — which here is `EvmHost` itself, not the original caller — so any leftover ETH is retained by the host contract rather than returned. If a caller sends a buffer above the expected cost (reasonable, since the exact AMM price at execution time is unknown), an MEV searcher can front-run the transaction to move the ETH/feeToken pool price, forcing the swap to consume a much larger portion of `msg.value` to acquire the fixed `post.fee` output, then back-run to restore the price and pocket the difference. Because `EvmHost` never forwards the swap's leftover ETH back to the caller, the entire delta between "fair price cost" and "sandwiched cost" — bounded only by the buffer the user supplied — is extractable by the attacker/lost to the contract, with no recourse for the payer.

This differs from a pure "hardcoded amountOutMin=0" bug (the output amount here is fixed, exact-output style) but shares the exact broken invariant from H-01: a public, unprivileged, fee-paying entrypoint performs an AMM swap with no caller-specified, price-bound protection, and the value siphoned off by the sandwich is not recoverable by the victim.

### Impact Explanation
Any user or contract calling `dispatch()`/`fundRequest()` with native token and a reasonable safety buffer can have that buffer's value extracted by an MEV searcher sandwiching the swap, and/or permanently lost into the `EvmHost` contract balance since there is no refund path — this is a direct loss-of-funds vector against ordinary protocol users, consistent with the Hyperbridge bounty's "stealing or loss of funds" category. It does not require a malicious relayer, prover, or admin — any public mempool searcher can trigger it.

### Likelihood Explanation
`dispatch()` and `fundRequest()` are the primary, frequently used, fully public entrypoints for interacting with Hyperbridge from EVM chains whenever a caller elects to pay fees in native token rather than pre-approved fee token — a documented, encouraged payment path. Every such call that includes any safety margin above the exact expected cost is exposed to sandwiching, making this a realistic, repeatedly triggerable condition rather than an edge case.

### Recommendation
- After the `swapETHForExactTokens` call in `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`, capture the `amounts[0]` actually spent and refund `msg.value - amounts[0]` to `_msgSender()`, mirroring the pattern already used in `IntentGatewayV2.placeOrder`/`fillOrder`.
- Additionally consider deriving `amountInMax` from an oracle-bound fair price (as done in `SimplexPaymaster.swapAndDeposit`, which computes `amountOutMin` from Chainlink feeds) rather than accepting the caller's entire `msg.value` unconstrained.

### Proof of Concept
1. Alice calls `EvmHost.dispatch{value: 5 ether}(DispatchPost{fee: X, ...})` expecting the swap to cost roughly 1 ETH for `X` fee-tokens, sending a 4 ETH buffer for safety (mirroring the exact scenario validated for `IntentGatewayV2` in `testPlaceOrder_FeeSwap_RefundsExcessNativeToken`, but here targeting `EvmHost.dispatch` directly).
2. An MEV searcher observes the pending transaction, front-runs it with a large ETH→feeToken swap on the same Uniswap V2 pool used by `_hostParams.uniswapV2`, pushing the price so that acquiring `X` fee-tokens now costs up to 4.9 ETH instead of 1 ETH.
3. Alice's transaction executes `swapETHForExactTokens(X, path, address(this), block.timestamp)` with `amountInMax = 5 ether` (her full `msg.value`), successfully spending 4.9 ETH to get exactly `X` fee-tokens; the router refunds the remaining 0.1 ETH to `EvmHost` (the caller of the swap), not to Alice.
4. The searcher back-runs to reverse their price manipulation, capturing the ~3.9 ETH of extracted value as profit.
5. `EvmHost.dispatch` returns without ever refunding Alice — even the 0.1 ETH genuinely unspent by the swap remains stuck in the `EvmHost` contract, since `dispatch()` has no refund step comparable to `IntentGatewayV2`'s handling.

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

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L3460-3499)
```text
    /// @notice placeOrder with fee swap refunds unused ETH after swapETHForExactTokens.
    function testPlaceOrder_FeeSwap_RefundsExcessNativeToken() public {
        uint256 inputAmount = 1000 * 1e6;
        uint256 feeAmount = 1 * 1e18; // 1 DAI worth of fees

        TokenInfo[] memory inputs = new TokenInfo[](1);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: inputAmount});

        TokenInfo[] memory outputAssets = new TokenInfo[](1);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 1000 * 1e18});

        PaymentInfo memory output =
            PaymentInfo({beneficiary: bytes32(uint256(uint160(user))), assets: outputAssets, call: ""});

        Order memory order = Order({
            user: bytes32(0),
            source: "",
            destination: host.host(),
            deadline: block.number + 1000,
            nonce: 0,
            fees: feeAmount,
            session: address(0),
            predispatch: DispatchInfo({assets: new TokenInfo[](0), call: ""}),
            inputs: inputs,
            output: output
        });

        uint256 userEthBefore = user.balance;

        vm.startPrank(user);
        usdc.approve(address(intentGateway), inputAmount);
        // Send 5 ETH for a fee swap that should cost much less
        intentGateway.placeOrder{value: 5 ether}(order, bytes32(0));
        vm.stopPrank();

        // User should get back most of the 5 ETH — the swap only needed a tiny fraction
        uint256 ethSpent = userEthBefore - user.balance;
        assertTrue(ethSpent < 1 ether, "User should have been refunded most of the 5 ETH");
        assertTrue(ethSpent > 0, "User should have spent some ETH on the fee swap");
    }
```
