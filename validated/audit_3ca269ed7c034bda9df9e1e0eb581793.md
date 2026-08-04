## Analysis

The Groupcoin bug's broken invariant: a **fixed native-token amount is forwarded to a swap intended to produce an exact output, the swap consumes less than what was sent, and the leftover is never returned to the payer** — it just accumulates in the contract with no path back to the depositor.

Hyperbridge's `EvmHost` has the exact same pattern in its core fee-dispatch functions, and — unlike its own `IntentGatewayV2`, which handles this correctly — it never captures or refunds the unspent ETH.

### Title
Unrefunded native-token overpayment in `EvmHost.dispatch`/`fundRequest` gets permanently trapped in the host contract - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest` accept `msg.value` and forward the **entire value** to `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(post.fee, ...)`, but never read the returned `amounts` array nor refund the unspent ETH to the caller [1](#0-0) . A standard router refunds unspent ETH to `msg.sender` of the swap call — which is `EvmHost` itself, not the original caller — so any overpayment is captured by the host and never returned [2](#0-1) . The same pattern repeats in `dispatch(DispatchGet)` [3](#0-2) .

### Finding Description
Contrast this with `IntentGatewayV2.placeOrder`, which does the same swap but explicitly captures the actual amount spent and refunds the rest to the caller:

```solidity
uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
    order.fees, path, address(this), block.timestamp
);
msgValue -= amounts[0];
...
if (msgValue > 0) {
    (bool sent,) = msg.sender.call{value: msgValue}("");
    if (!sent) revert InsufficientNativeToken();
}
``` [4](#0-3) 

`EvmHost`'s own `dispatch`/`fundRequest` functions omit this refund step entirely — they discard the `amounts` return value and never touch `_msgSender()` again after the swap [5](#0-4) . Since `swapETHForExactTokens` requires only an `amountInMaximum` (here, the full `msg.value`) to buy an exact `post.fee`/`get.fee`/`amount` of fee token, any caller who doesn't compute `msg.value` to the exact wei needed for the swap loses the difference — the AMM price at execution time is generally unknown to the caller in advance, so overpayment by any nontrivial margin is the normal, expected case, not an edge case.

The only recovery path is `IHostManager.withdraw(WithdrawParams)`, reachable exclusively through a cross-chain governance message routed via `HostManager.onAccept`, gated to messages whose `source` is the Hyperbridge parachain [6](#0-5) . `WithdrawParams` lets governance choose an arbitrary `beneficiary` for the withdrawn native token [7](#0-6)  — there is no on-chain accounting tying trapped overpayment back to the specific user who sent it, so the funds are not just locked but effectively become fungible "protocol revenue" swept to whatever address governance names, not necessarily refunded to the original overpaying user.

### Impact Explanation
Any unprivileged user calling `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, or `fundRequest` with native ETH permanently loses any amount sent beyond what the AMM swap actually consumes. This is direct loss of user funds through the protocol's own public entry point, with no self-service recovery — matching "stealing or loss of funds" under the bounty's impact gate. It requires no malicious peer, relayer, or admin; it happens on every ordinary overpaid dispatch.

### Likelihood Explanation
Likelihood is high: `msg.value` for these calls must be estimated off-chain against a live AMM price that moves before the transaction lands, so any caller using conservative/rounded gas-cost estimates (as documented in Hyperbridge's own fee guidance, e.g. `destination_gas_cost = 150_000 + receiving_module_gas_cost` plus a service-fee markup [8](#0-7) ) will routinely send more ETH than the swap consumes. This is the default, unavoidable interaction pattern for any self-relaying dApp or user paying in native token, not a rare misuse case.

### Recommendation
Capture the `amounts` array returned by `swapETHForExactTokens` in `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest`, and refund `msg.value - amounts[0]` to `_msgSender()` immediately after the swap, exactly as `IntentGatewayV2.placeOrder` already does.

### Proof of Concept
1. User calls `EvmHost.dispatch{value: 1 ether}(DispatchPost{fee: X, ...})` where the AMM only needs 0.1 ETH to buy `X` fee tokens.
2. `swapETHForExactTokens{value: 1 ether}(X, path, address(this), block.timestamp)` executes; the router spends 0.1 ETH and refunds 0.9 ETH back to `EvmHost` (the caller of the router).
3. `EvmHost.dispatch` returns without ever inspecting the swap's `amounts` output or forwarding any ETH back to the user.
4. The user's 0.9 ETH overpayment is now held by `EvmHost` indefinitely, only movable via a Hyperbridge-governance-originated `withdraw` call to a beneficiary of governance's choosing [9](#0-8)  — the original user has no way to reclaim it themselves.

### Citations

**File:** evm/src/core/EvmHost.sol (L88-96)
```text
// Withdrawal parameters
struct WithdrawParams {
    // The beneficiary address
    address beneficiary;
    // the amount to be disbursed
    uint256 amount;
    // Withdraw the native token?
    address token;
}
```

**File:** evm/src/core/EvmHost.sol (L921-959)
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

        // adjust the timeout
        uint64 timeoutTimestamp = post.timeout == 0 ? 0 : uint64(block.timestamp) + uint64(post.timeout);
        PostRequest memory request = PostRequest({
            source: host(),
            dest: post.dest,
            nonce: uint64(_nextNonce()),
            from: abi.encodePacked(_msgSender()),
            to: post.to,
            timeoutTimestamp: timeoutTimestamp,
            body: post.body
        });

        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: post.payer, fee: post.fee});
        emit PostRequestEvent({
            source: string(request.source),
            dest: string(request.dest),
            from: _msgSender(),
            to: abi.encodePacked(request.to),
            nonce: request.nonce,
            timeoutTimestamp: request.timeoutTimestamp,
            body: request.body,
            fee: post.fee
        });
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

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L103-113)
```text
**Relayer Fee Calculation:**

The relayer fee should cover:
1. **Gas costs on destination** - This includes:
   - **Proof verification** (~150k gas) - Fixed cost for verifying state proofs on the destination chain
   - **Execution gas** - Gas consumed by your contract's `IApp.onAccept` handler. 

2. **Relayer service fee** - Incentive for relayer services (typically 10-20% markup on gas costs)

**Refund on Timeout:**
If the request times out, the `payer` address receives the relayer fee back.
```
