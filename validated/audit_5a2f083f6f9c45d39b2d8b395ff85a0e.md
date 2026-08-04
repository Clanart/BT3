Found a genuine local analog: unlike `IntentGatewayV2`, which explicitly refunds unspent native tokens to the caller, `EvmHost`'s payable dispatch functions swallow any ETH overpayment permanently into the contract, recoverable only via privileged governance withdrawal to an arbitrary beneficiary — not necessarily the original payer.

### Title
Excess native-token overpayment in `EvmHost.dispatch()`/`fundRequest()` is permanently trapped and never refunded to the payer - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` are `payable` and accept native ETH as an alternate payment method for protocol fees, internally swapping it to `feeToken()` via `swapETHForExactTokens`. None of these functions refund any unspent `msg.value` to the caller after the swap, unlike the sibling app contract `IntentGatewayV2`, which explicitly implements this refund.

### Finding Description
In `EvmHost.sol`, the fee-paying dispatch functions perform: [1](#0-0) 

`IUniswapV2Router02.swapETHForExactTokens` only guarantees `post.fee`/`get.fee`/`amount` worth of `feeToken()` output; per the standard UniswapV2 router implementation, any leftover ETH (`msg.value - amounts[0]`) is refunded to `msg.sender` of the router call — which is `EvmHost` itself (`address(this)`), not the original transaction sender (`_msgSender()`). The function then proceeds directly to building/committing the request without ever comparing the ETH actually consumed to `msg.value` or forwarding a refund back to the caller: [2](#0-1) 

This is confirmed by the comment on the contract's `receive()`, which describes overshoot ETH as "dust" collected by the host rather than something owed back to the user: [3](#0-2) 

Compare this to `IntentGatewayV2`, which performs the identical swap pattern but explicitly returns unspent value to `msg.sender`: [4](#0-3) 

The only way to move ETH back out of `EvmHost` is the privileged `withdraw()` entrypoint, restricted to `_hostParams.hostManager` (cross-chain governance) and sent to whatever `beneficiary` governance specifies — not necessarily the overpaying user: [5](#0-4) 

### Impact Explanation
Any caller of `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, or `fundRequest()` who sends `msg.value` greater than the ETH-equivalent cost of the fee (a very likely outcome given users must estimate swap slippage/price in advance, since `swapETHForExactTokens` takes an exact-output amount) permanently loses the difference into the host contract's balance. Since normal users/integrators have no direct claim mechanism, and the only path to recovery is a `restrict(_hostParams.hostManager)`-gated withdrawal that sends funds to an arbitrary governance-chosen beneficiary, this is a genuine, unprivileged, and repeatable loss-of-funds bug reachable via the production `dispatch`/`fundRequest` public entrypoints used by every app that pays for cross-chain requests in native token.

### Likelihood Explanation
High. `dispatch()` documents native-token payment as a first-class, encouraged option ("Payment for the request can be made with either the native token or the feeToken"), and any caller who doesn't compute the exact optimal `msg.value` (which requires knowing the router's exact quote at execution time, subject to same-block price movement) will overpay and lose the difference on every single call. No malicious actor, relayer, or governance compromise is required — it's triggered by ordinary usage of a public entrypoint.

### Recommendation
Mirror `IntentGatewayV2`'s pattern in all three `EvmHost` payable functions: track the ETH balance before/after the swap (or capture the router's returned `amounts[0]`) and refund `msg.value - amounts[0]` directly to `_msgSender()` in the same transaction, reverting on failed refund transfer just as `IntentGatewayV2` does with `InsufficientNativeToken`.

### Proof of Concept
1. Configure `EvmHost` with a live `uniswapV2` router and `feeToken`.
2. Call `dispatch(DispatchPost{ ..., fee: F })` supplying `msg.value = V` where `V` is meaningfully larger than the ETH needed to buy `F` units of `feeToken` (e.g., due to conservative slippage buffering, which is unavoidable since `swapETHForExactTokens` reverts if `V` is insufficient, incentivizing callers to over-supply).
3. `swapETHForExactTokens{value: V}(F, path, address(this), ...)` executes; the router refunds `V - amounts[0]` back to `address(this)` (EvmHost), landing in `EvmHost`'s ETH balance via its `receive()`.
4. `dispatch()` returns `commitment` without ever moving that residual ETH back to the caller.
5. The caller has no function to reclaim it; only governance's `withdraw()` can move it, to any `beneficiary` it chooses — demonstrating the funds are effectively lost to the original payer.

### Citations

**File:** evm/src/core/EvmHost.sol (L383-386)
```text
    /*
     * @dev receive function for UniswapV2Router02, collects all dust native tokens.
     */
    receive() external payable {}
```

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

**File:** evm/src/core/EvmHost.sol (L1031-1051)
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

        FeeMetadata memory metadata = _requestCommitments[commitment];
        if (metadata.sender == address(0)) revert UnknownRequest();

        metadata.fee += amount;
        _requestCommitments[commitment] = metadata;

        emit RequestFunded({commitment: commitment, newFee: metadata.fee});
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
