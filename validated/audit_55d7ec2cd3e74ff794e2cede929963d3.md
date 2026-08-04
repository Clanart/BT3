### Title
Native-fee dispatch swaps use `swapETHForExactTokens` with no user-facing refund path, permanently trapping any over-supplied ETH in `EvmHost` - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` all let a caller pay the relayer/protocol fee in native token by forwarding `msg.value` straight into `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(fee, path, address(this), block.timestamp)`. [1](#0-0) [2](#0-1) [3](#0-2) 

Exactly like the AuraSpell H-3 pattern where users are forced into a swap they cannot parameterize, here the user cannot control slippage/refund of the *input* side: `swapETHForExactTokens` refunds any unused `msg.value` to `msg.sender` **of the router call**, which is `EvmHost`, not the EOA/contract that originally called `dispatch`/`fundRequest`. There is no code path in `EvmHost` that forwards this refunded dust back to `_msgSender()`.

### Finding Description
The docs explicitly warn that `quote()` (used off-chain to size `msg.value`) is only an approximation subject to sandwich/slippage risk, so callers must send extra ETH as a buffer: [4](#0-3) 

When `dispatch`/`fundRequest` is called with `msg.value` larger than what the swap actually consumes (due to normal price movement, a slightly generous client-side buffer, or an attacker sandwiching the swap to worsen the price and shrink the leftover — either way, leftover ETH exists), UniswapV2Router02's `swapETHForExactTokens` computes `amounts[0]` (ETH actually spent) and refunds `msg.value - amounts[0]` via `TransferHelper.safeTransferETH(msg.sender, ...)`. The `msg.sender` as seen by the router is `EvmHost` itself, because `EvmHost` is the contract invoking the router with `{value: msg.value}`: [1](#0-0) 

After the swap call returns, `dispatch()` immediately proceeds to build the request/commitment and emit the event — there is no logic that reads `address(this).balance`, computes the delta, and forwards it back to `_msgSender()`: [5](#0-4) 

The same pattern repeats in the GET dispatch path and in `fundRequest`: [6](#0-5) [7](#0-6) 

The refunded native ETH is therefore captured as general contract balance. Withdrawal of `EvmHost`'s native balance is only reachable through the governance-only `IHostManager.withdraw` path (`restrict(_hostParams.hostManager)`), never by the original payer. This means user funds move to a party (the host/governance treasury) that is not "the rightful beneficiary" of that specific payment — the ETH was never intended as a donation, it was intended entirely for a fee swap and any unused remainder belongs to the caller.

This is a direct local analog of the H-3 bug class: users are forced through a swap they cannot fully control the economics of, and any resulting surplus/slippage-refund is not returned to them but is siloed away, permanently, from the rightful owner.

### Impact Explanation
Every native-token-funded `dispatch()`, `dispatch(DispatchGet)`, or `fundRequest()` call that supplies `msg.value` in excess of the exact amount the router consumes results in that excess being permanently absorbed into `EvmHost`'s balance rather than returned to the caller. Because `quote()`/`getAmountsIn` is explicitly documented as an approximate, sandwich-able off-chain estimate, real-world callers routinely need to over-supply `msg.value` as a slippage buffer, guaranteeing dust/loss on every native-fee transaction at scale. This is a genuine, protocol-level loss of user funds with no recovery path for the payer — the funds are only recoverable by whichever entity controls `hostManager` governance, not the depositor.

### Likelihood Explanation
High. Any unprivileged user calling the native-token-payment path in `dispatch`/`fundRequest` triggers this every time they supply more ETH than the router's live price requires — which is unavoidable in practice since the docs themselves say the client-side quote can't be trusted for exact sizing and must be over-estimated to survive sandwiching/normal price drift. No malicious relayer, prover, or admin is required — this is purely a self-inflicted design gap in `EvmHost` triggered by ordinary usage.

### Recommendation
After each `swapETHForExactTokens` call in `dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest`, compute the actual ETH consumed (`amounts[0]` returned by the swap) and refund `msg.value - amounts[0]` back to `_msgSender()` (or `post.payer`/`get`'s payer, consistent with the fee-refund semantics already used elsewhere in the contract), instead of leaving the dust in the contract's balance.

### Proof of Concept
1. Attacker or ordinary user calls `EvmHost.dispatch(DispatchPost)` with `msg.value = 1.05 ether` intending to cover a fee that, per `quote()`, needs ~1 ether worth of native to buy `post.fee` in feeToken, using the extra 0.05 ether as slippage buffer as the docs recommend.
2. `swapETHForExactTokens{value: 1.05 ether}(post.fee, path, address(this), block.timestamp)` executes; suppose actual price only requires 0.98 ether. The router refunds `1.05 - 0.98 = 0.07 ether` to `msg.sender`, i.e., to `EvmHost`. [8](#0-7) 
3. `dispatch()` continues, builds the commitment, and returns — the 0.07 ether refund is never sent back to the caller; it simply becomes part of `EvmHost`'s native balance.
4. That balance is only withdrawable via `IHostManager.withdraw`, restricted to `_hostParams.hostManager` — the original payer has no way to reclaim it. [9](#0-8) 

This can be repeated on every native-fee dispatch/fundRequest call, accumulating stranded user ETH in the host contract with no user-facing recovery mechanism.

### Citations

**File:** evm/src/core/EvmHost.sol (L74-96)
```text
interface IHostManager {
    /**
     * @dev Updates IsmpHost params
     * @param params new IsmpHost params
     */
    function updateHostParams(HostParams memory params) external;

    /**
     * @dev withdraws bridge revenue to the given address
     * @param params, the parameters for withdrawal
     */
    function withdraw(WithdrawParams memory params) external;
}

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

**File:** evm/src/core/EvmHost.sol (L933-959)
```text

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

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L236-238)
```text
<Callout type="warning" title="Estimate Fees Off-Chain">
Use the `quote()` view function from your frontend to estimate how much native token users need to send. **Do not call `quote()` in smart contract transactions.** It uses Uniswap's `getAmountsIn`, making it vulnerable to sandwich attacks. Only use it off-chain for frontend fee estimation
</Callout>
```
