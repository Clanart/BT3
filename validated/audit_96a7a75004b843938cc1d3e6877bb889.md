### Title
Excess native token sent to `EvmHost.dispatch()` / `fundRequest()` is silently retained by the host instead of refunded to the caller - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` all accept `msg.value` and forward it in full to `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(...)`, an exact-output swap that only consumes as much ETH as needed to buy `post.fee`/`get.fee`/`amount` of the fee token and refunds the remainder to whichever address called the router.

### Finding Description
Because `EvmHost` itself is the caller of the router (not the end user), any leftover ETH from the exact-output swap is refunded back into `EvmHost`'s own balance rather than to the original `msg.sender` that funded the dispatch call: [1](#0-0) [2](#0-1) [3](#0-2) 

None of these three functions capture the `amounts` return value of `swapETHForExactTokens` or perform any refund of unspent native token to `_msgSender()`. This is in stark contrast to the pattern used elsewhere in the same codebase, e.g. `IntentGatewayV2.placeOrder`, which explicitly captures `amounts[0]` and refunds `msgValue - amounts[0]` back to the user: [4](#0-3) 

and the standalone Uniswap wrappers (`UniV3UniswapV2Wrapper.sol` / `UniV4UniswapV2Wrapper.sol`), which snapshot balances and forward the refund to the actual caller.

The only path by which this trapped native ETH can leave `EvmHost` is the governance-only `withdraw()` function, callable exclusively by the `hostManager` contract, itself only reachable via a Hyperbridge-originated governance message: [5](#0-4) [6](#0-5) 

So any user overpaying `msg.value` on `dispatch()`/`fundRequest()` (a very easy mistake — the natural expectation, matching every other quote/swap pattern in this repo, is "excess is refunded") permanently and irreversibly loses that excess to the protocol's host-controlled balance, extractable only by a privileged governance actor later.

### Impact Explanation
Every unprivileged caller of `IDispatcher.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, or `fundRequest()` who sends more native token than the pegged `fee`/`amount` requires (e.g. quoting slightly generously to survive router price movement, a common and encouraged pattern per the SDK's `quote()`/`quoteNative()` helpers which add safety buffers) has that excess permanently redirected into `EvmHost`'s balance instead of returned to them. This is a direct, protocol-level loss of end-user funds on every dispatch call that isn't a knife's-edge exact amount, with no way for the user to reclaim it — only governance can later withdraw it.

### Likelihood Explanation
High. This triggers on ordinary use, not on some adversarial condition: any application or user integrating with `dispatch()`/`fundRequest()` and rounding their `msg.value` up (which the SDK's own `quote()` methods do, by design, adding buffers) loses the difference every single time. No malicious relayer, prover, or governance actor is required — the bug fires deterministically for any overpaying caller.

### Recommendation
Capture the `amounts` array returned by `swapETHForExactTokens` in all three call sites (`dispatch(DispatchPost)`, `dispatch(DispatchGet)`, `fundRequest`) and refund `msg.value - amounts[0]` back to `_msgSender()`, mirroring the pattern already implemented in `IntentGatewayV2.placeOrder`. Alternatively, harden further by measuring `address(this).balance` before/after the swap call rather than trusting the router's return value, consistent with the report's underlying recommendation to not trust untrusted external-call return values for value accounting.

### Proof of Concept
1. A user calls `EvmHost.dispatch(DispatchPost{... fee: 1e18, ...})` and sends `msg.value = 2 ether` (e.g. because they quoted the fee with a safety buffer, or the ETH/feeToken price moved favorably between quoting and submission).
2. `EvmHost.dispatch` forwards the full 2 ether to `swapETHForExactTokens{value: 2 ether}(1e18, path, address(this), block.timestamp)`.
3. The router swaps only enough ETH to obtain exactly `1e18` fee tokens (say 1.2 ether worth) and refunds the remaining ~0.8 ether — but the refund target is `address(this)` = `EvmHost`, not the original user.
4. `EvmHost`'s ETH balance increases by 0.8 ether that the user can never reclaim; the `dispatch` function returns normally with no error and no refund transfer to the caller.
5. Repeat for any caller of `dispatch(DispatchGet)` or `fundRequest()` — same result.

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

**File:** evm/src/core/EvmHost.sol (L1031-1040)
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

**File:** evm/src/core/HostManager.sol (L95-109)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
        PostRequest calldata request = incoming.request;
        // Only the Hyperbridge parachain can send requests to this module.
        if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
        if (action == OnAcceptActions.Withdraw) {
            // This is where governance & relayers can withdraw their revenue.
            WithdrawParams memory withdrawParams = abi.decode(request.body[1:], (WithdrawParams));
            IHostManager(_params.host).withdraw(withdrawParams);
        } else if (action == OnAcceptActions.SetHostParam) {
            HostParams memory hostParams = abi.decode(request.body[1:], (HostParams));
            IHostManager(_params.host).updateHostParams(hostParams);
        }
    }
```
