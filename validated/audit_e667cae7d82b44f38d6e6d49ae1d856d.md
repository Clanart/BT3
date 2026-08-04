## Analysis

The Anycall bug's core defect is: a contract forwards a **user-supplied native-token value** into a cross-chain primitive, but the excess/leftover value from that external call is not correctly returned to the party that is entitled to it. The Hyperbridge analog is in `EvmHost.dispatch()`'s native-token fee-payment path, which forwards `msg.value` into a Uniswap V2 swap and never reclaims the router's leftover-ETH refund for the actual caller — the refund lands on the `EvmHost` contract itself instead. [1](#0-0) 

### Title
Native-token overpayment in `EvmHost.dispatch()` is permanently lost — swap refund goes to the contract, not the caller - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)` and `EvmHost.dispatch(DispatchGet)` accept `msg.value` and, when non-zero, call `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(post.fee, path, address(this), block.timestamp)`. [1](#0-0) [2](#0-1) 

`swapETHForExactTokens` swaps only the exact input required to obtain `post.fee`/`get.fee` output tokens and refunds any unused ETH — but that refund is sent to `msg.sender` of the router call, which in this context is `address(EvmHost)`, not the original transaction caller who supplied `msg.value`. `EvmHost` has no `receive()`/fallback-driven sweep of native ETH back to callers, and its only documented revenue-withdrawal path (`IHostManager.withdraw` via `HostManager.onAccept`) is scoped to fee-token revenue, not stray native ETH. [3](#0-2) 

By contrast, other contracts in the same codebase that perform the identical “ETH-for-exact-fee-token” pattern *do* explicitly track and refund the unspent portion of `msg.value` back to the caller, e.g. `IntentGatewayV2.sol` (`msgValue -= amounts[0]`) and `ExtrinsicIntents.sol` (`if (msgValue > 0) { (bool sent,) = msg.sender.call{value: msgValue}(""); ... }`), confirming that `EvmHost.dispatch()` deviates from the codebase's own established safe pattern. [4](#0-3) [5](#0-4) 

### Finding Description
The dispatch documentation explicitly instructs application developers to forward `msg.value` verbatim into `IDispatcher(_host).dispatch{value: msg.value}(post)`, i.e. exact fee estimation by the caller is not required or guaranteed by protocol design — overestimation is expected as normal usage. [6](#0-5) 

Whenever the forwarded `msg.value` exceeds the ETH amount actually needed to buy `post.fee`/`get.fee` units of the fee token (which is the common case, since ETH/token price fluctuates between the moment a client estimates gas and the moment the transaction executes), `swapETHForExactTokens` computes `amounts[0] <= msg.value` as the exact input spent and refunds `msg.value - amounts[0]` to whichever address called the router — `EvmHost`. `EvmHost.dispatch()` never reads or forwards that refund onward to `_msgSender()`; it simply proceeds to build the `PostRequest`/`GetRequest` and emits the commitment. The leftover native ETH is stranded as an unaccounted balance on `EvmHost`.

### Impact Explanation
This is a direct, protocol-level loss of user funds: any unprivileged caller who dispatches a POST/GET request using native-token payment loses whatever ETH they overpaid beyond the exact fee-token cost, with no mechanism in the contract to recover it. Given that overpayment is the expected/encouraged usage pattern (documentation forwards `msg.value` directly, and callers cannot know the exact Uniswap price at submission time), this is not an edge case — it is a systemic leak of user value on every native-token dispatch that isn't pennies-precise. The stranded ETH is also not part of the protocol's fee-token revenue accounting, so it is not even recoverable via the legitimate governance withdrawal path in `HostManager`, meaning it is permanently locked in the `EvmHost` contract.

### Likelihood Explanation
High. This triggers on the ordinary, documented usage path (`dispatch{value: msg.value}(post)`) with no attacker action required beyond calling a public, permissionless entrypoint (`dispatch`) with slightly more ETH than the exact swap requires — which is unavoidable in practice given the swap's price sensitivity and any reasonable client-side gas/fee buffering.

### Recommendation
Track the router's actual spent amount (`amounts[0]`) from `swapETHForExactTokens`'s return value and refund `msg.value - amounts[0]` back to `_msgSender()` before returning, mirroring the pattern already used in `IntentGatewayV2.sol` and `ExtrinsicIntents.sol`.

### Proof of Concept
1. Caller estimates `post.fee = 100` fee-token units and, to be safe against price movement, sends `msg.value = 1 ether` to `EvmHost.dispatch(post)`.
2. Inside `dispatch()`, `swapETHForExactTokens{value: 1 ether}(100, path, address(this), block.timestamp)` executes: suppose the true cost is `0.4 ether`.
3. The Uniswap router computes `amounts[0] = 0.4 ether`, performs the swap, and refunds `1 ether - 0.4 ether = 0.6 ether` to `msg.sender` of the router call — which is `EvmHost`, not the caller.
4. `dispatch()` continues, building `PostRequest`, storing `FeeMetadata`, and returning `commitment`; the `0.6 ether` is never returned to the caller and has no code path to reach them again.
5. Repeating this for every dispatch call permanently accumulates stranded ETH in `EvmHost` with no withdrawal function available to recover it for the affected users. [1](#0-0)

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

**File:** evm/src/core/HostManager.sol (L95-108)
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L345-356)
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

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L52-70)
```text
```solidity lineNumbers title="MyApp.sol"
function sendMessage(
    bytes memory message,
    uint64 timeout,
    address to,
    uint256 relayerFee
) public payable returns (bytes32) {
    DispatchPost memory post = DispatchPost({
        body: message,
        dest: StateMachine.evm(1),
        timeout: timeout,
        to: abi.encode(to),
        fee: relayerFee,
        payer: msg.sender
    });

    return IDispatcher(_host).dispatch{value: msg.value}(post);
}
```
```
