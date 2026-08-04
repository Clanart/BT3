## Finding [1](#0-0) 

`IntentGatewayV2.cancelOrder` is declared `payable` and routes to three internal handlers depending on order type. For **same-chain** orders it calls `_cancelSameChain`, which never inspects, forwards, or refunds `msg.value`: [2](#0-1) 

Compare this to the two cross-chain branches, `_cancelFromSource` and `_cancelFromDest`, which explicitly branch on `msg.value > 0` to pay dispatch fees via Uniswap swap or fall back to the fee token: [3](#0-2) [4](#0-3) 

And compare to `fillOrder`, which is careful to refund any unspent native value back to the caller at the end of execution: [5](#0-4) 

`_cancelSameChain` has no such msg.value handling — no dispatch, no swap, no refund — and `cancelOrder` is `nonReentrant` and `payable` at the top level, so any ETH attached to a same-chain cancel call is simply absorbed into the contract's balance with no accounting path to ever return it.

### Title
`IntentGatewayV2.cancelOrder` same-chain path is payable but never consumes or refunds `msg.value`, permanently locking sent ETH - (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
`cancelOrder` is marked `payable` because two of its three routing branches (source/destination cross-chain cancel) need `msg.value` to pay the Hyperbridge dispatch fee. The third branch, same-chain cancellation via `_cancelSameChain`, requires no cross-chain messaging at all and therefore has no fee to pay — yet the function accepts `msg.value` without ever using or refunding it.

### Finding Description
`cancelOrder` dispatches to `_cancelSameChain` whenever `order.source == order.destination`: [6](#0-5) 

`_cancelSameChain` only validates the caller, checks escrow balances, and calls `_withdraw` to return the escrowed order inputs to `order.user`. It contains no reference to `msg.value`, no dispatch call, and no refund logic: [2](#0-1) 

If a caller (the SDK, a bot, or a user manually constructing the transaction) attaches native ETH to a same-chain `cancelOrder` call — e.g., by reusing calldata/value assembly logic built for the cross-chain routes, or simply overestimating the required fee — that ETH is transferred into the `IntentGatewayV2` contract and is never moved anywhere afterward. There is no `receive()`/sweep/refund mechanism tied to this code path that would return it.

This is the direct structural analog of the reported `mint()` bug: a `payable` entrypoint whose specific execution branch has no interaction with `msg.value`, so accidentally-sent value is irrecoverably locked in the contract.

### Impact Explanation
Any native token value sent along with a same-chain `cancelOrder` transaction is permanently stranded in the `IntentGatewayV2` contract with no code path to recover it — the classic "loss of funds" impact class from the bounty scope (funds move to nowhere/lock rather than to the rightful beneficiary). Because `cancelOrder` is a single public entrypoint shared across all three routing modes, and only the cross-chain modes need `msg.value`, users following any tooling, documentation, or wallet flow that defaults to attaching a native fee (as the cross-chain routes do) will silently lose that ETH the moment their order happens to be same-chain.

### Likelihood Explanation
Any unprivileged caller can trigger this by calling the public `cancelOrder` function with `msg.value > 0` on a same-chain order — no malicious peer, relayer, prover, or admin is required. The SDK's cancellation flow quotes and attaches a native fee for cross-chain routes; a caller building the same-chain transaction manually (or a client misconfiguration that reuses the cross-chain code path) reaches this branch trivially.

### Recommendation
Either make `_cancelSameChain`'s caller path non-payable (reject `cancelOrder` calls with `msg.value > 0` when `isSameChain` is true, via `if (msg.value > 0) revert(...)`), or explicitly refund `msg.value` to `msg.sender` at the end of `_cancelSameChain`, mirroring the unspent-value refund already implemented in the fill path.

### Proof of Concept
1. User places a same-chain order via `placeOrder` (`order.source == order.destination`).
2. User (or any caller authorized per `_cancelSameChain`'s `order.user` check) calls `cancelOrder(order, options)` and attaches `msg.value = X` (e.g., mistakenly assuming a relayer fee is always required, as it is for cross-chain cancels).
3. `cancelOrder` routes to `_cancelSameChain` since `orderSource == orderDest`.
4. `_cancelSameChain` refunds only the escrowed order inputs via `_withdraw`; `X` ETH remains in the contract's balance.
5. No subsequent call in the contract can return `X` to the original sender — it is permanently locked (absent a protocol-level sweep to some other beneficiary, which would itself constitute wrong-beneficiary fund movement).

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L470-490)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable nonReentrant {
        bytes32 commitment = keccak256(abi.encode(order));

        if (_filled[commitment] != address(0)) revert Filled();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain) {
            _cancelSameChain(order, commitment);
        } else if (currentChain == orderSource) {
            _cancelFromSource(order, options, commitment);
        } else if (currentChain == orderDest) {
            _cancelFromDest(order, options, commitment);
        } else {
            revert WrongChain();
        }
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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L161-187)
```text
    function _cancelSameChain(Order calldata order, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        if (orderSource != currentChain) revert WrongChain();

        uint256 inputsLen = order.inputs.length;
        TokenInfo[] memory remainingTokens = new TokenInfo[](inputsLen);
        bool hasEscrow = false;
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            uint256 escrowed = _orders[commitment][token];
            if (escrowed > 0) hasEscrow = true;
            remainingTokens[i] = TokenInfo({token: order.inputs[i].token, amount: escrowed});
            unchecked {
                ++i;
            }
        }
        if (!hasEscrow) revert UnknownOrder();

        WithdrawalRequest memory body =
            WithdrawalRequest({commitment: commitment, tokens: remainingTokens, beneficiary: order.user});

        _withdraw(body, true, true);
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L217-223)
```text
        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L261-267)
```text
        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
```
