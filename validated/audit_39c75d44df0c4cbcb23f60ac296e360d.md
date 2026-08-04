Based on my investigation, I found a genuine local analog: **`EvmHost.sol`'s native-fee-swap paths use exact-output Uniswap V2 swaps but never refund the swap's leftover ETH to the original caller**, unlike the sibling contract `IntentGatewayV2.sol` which does perform this refund. This is not the identical "missing `amountOutMin`" bug from the Cork report (these calls do bound output via exact-output swaps), but it is the same *bug class*: an AMM interaction in a fund-custody path with no protection for the caller's leftover/excess value, leading to real, permanent loss of user funds.

### Title
Native fee payments to `EvmHost.dispatch()`/`fundRequest()` permanently strand excess ETH instead of refunding the caller - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` all accept `msg.value` and swap it via `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(fee, path, address(this), block.timestamp)` to buy an exact amount of `feeToken()`. [1](#0-0) [2](#0-1) [3](#0-2) 

Uniswap V2's `swapETHForExactTokens` only spends as much ETH as required for the exact output and refunds the unspent remainder to `msg.sender` of that call. Here, the caller of the router is `EvmHost` itself (it forwards `msg.value` from the *user*), so any refund lands in `EvmHost`'s own balance, not back to the user who overpaid. None of these three functions track or return the leftover ETH to the original caller.

### Finding Description
Compare this to `IntentGatewayV2.placeOrder()`, which performs the same kind of ETH→feeToken swap but explicitly reduces `msgValue` by the amount actually spent and refunds the remainder to `msg.sender` at the end of the function: [4](#0-3) 

`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` contain no equivalent accounting or refund step — after the swap call, execution proceeds directly to constructing the request/commitment with no reference to unspent `msg.value`: [5](#0-4) [6](#0-5) [7](#0-6) 

Since users generally cannot predict the exact ETH/feeToken price at execution time, they must send `msg.value` with some buffer above the expected required amount to avoid a revert from `swapETHForExactTokens` (which reverts if `msg.value` is insufficient). Any buffer sent is a swap-derived refund that is trapped inside `EvmHost`, indistinguishable from the contract's other funds, with no user-facing function to reclaim it. This is exactly the missing-protection-during-AMM-interaction pattern from the seed report, applied to Hyperbridge's core fee-collection path instead of an LP-removal path — the "value" that should return to the rightful owner (the fee payer) instead permanently accrues to the protocol/host contract.

### Impact Explanation
This is a direct, unprivileged loss of user funds: any application or user calling `dispatch()`/`fundRequest()` with native ETH and providing a safety buffer above the exact fee amount permanently loses that buffer — no malicious relayer, prover, or governance actor is required. Given that `dispatch()` is the primary path documented for sending cross-chain messages with native-token fee payment across the SDK and docs, this affects a large volume of user-initiated transactions, not just an edge case. [8](#0-7) 

### Likelihood Explanation
High. The buffer overpayment scenario is the *expected* normal usage pattern (users cannot know the exact live AMM price at submission time, and any underestimate causes a revert, encouraging generous buffers). No special conditions, front-running, or malicious actors are needed — it happens on every native-fee dispatch call where `msg.value` exceeds the exact amount consumed by the swap.

### Recommendation
In `EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()`, capture the `amounts[0]` (ETH actually spent) returned by `swapETHForExactTokens` and refund `msg.value - amounts[0]` back to `_msgSender()`, mirroring the pattern already implemented in `IntentGatewayV2.placeOrder()`.

### Proof of Concept
1. A user calls `EvmHost.dispatch(DispatchPost)` with `post.fee = 100` (feeToken units) and sends `msg.value = 1 ether` as a safety buffer, expecting the swap to only consume the ETH equivalent of 100 units of `feeToken`.
2. Internally, `swapETHForExactTokens{value: 1 ether}(100, path, address(this), block.timestamp)` executes; suppose only `0.4 ether` is needed to buy exactly 100 `feeToken` units.
3. The router refunds `0.6 ether` to `msg.sender` of the swap call — which is `EvmHost`, not the user.
4. `dispatch()` proceeds to build and emit the request without ever inspecting or returning this `0.6 ether`; it now sits in `EvmHost`'s balance.
5. The user has no function to reclaim the `0.6 ether`; it is permanently lost to them (and effectively becomes protocol-controlled funds without any user consent or accounting), matching `IntentGatewayV2.placeOrder()`'s explicit acknowledgment that such refunds are owed to the caller.

**Uncertainty**: I did not find a `receive()`/fallback or any downstream sweep mechanism in `EvmHost.sol` that could reroute this stranded ETH back to users; my search of the file for `refund`/`receive()`/`msg.value -` patterns returned no matches outside the `IntentGatewayV2` comparison. It's possible governance (`hostManager`) can later withdraw this ETH as "protocol revenue," but no path returns it to the specific overpaying user, which is the core issue.

### Citations

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

**File:** evm/src/core/EvmHost.sol (L974-1013)
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

        uint64 timeoutTimestamp = get.timeout == 0 ? 0 : uint64(block.timestamp) + uint64(get.timeout);
        GetRequest memory request = GetRequest({
            source: host(),
            dest: get.dest,
            nonce: uint64(_nextNonce()),
            from: abi.encodePacked(_msgSender()),
            timeoutTimestamp: timeoutTimestamp,
            keys: get.keys,
            height: get.height,
            context: get.context
        });

        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: _msgSender(), fee: get.fee});
        emit GetRequestEvent({
            source: string(request.source),
            dest: string(request.dest),
            from: request.from,
            keys: request.keys,
            nonce: request.nonce,
            height: request.height,
            context: request.context,
            timeoutTimestamp: request.timeoutTimestamp,
            fee: get.fee
        });
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

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L115-127)
```text
## Payment Methods

The Hyperbridge protocol ultimately collects its fees in the `feeToken` (usually a stablecoin). But it can also accept native token payments, which are automatically swapped for the `feeToken` using the local AMMs like Uniswap.


| Token | Payment Method |
|-------|----------------|
| Native token (ETH, BNB, POL, DOT etc.) | Sent with transaction via `msg.value` |
| Fee token (set by IsmpHost) | Requires ERC20 approval before dispatch |

<Callout type="info">
For testing purposes on testnet, you can use the testnet fee token, [Hyper USD](https://sepolia.etherscan.io/address/0xBe97E73126d66188d72FBf99029126d0340a7f18). The contract address is the same across several EVM chains. Check out the [guide](/developers/guides/testnet-fee-token) on how to get the token. 
</Callout>
```
