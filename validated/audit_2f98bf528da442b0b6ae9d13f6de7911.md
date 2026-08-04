## Analysis Summary

The external report's core broken invariant: **an unprotected, un-isolated external call to a single third-party/governance-configured contract sits directly in the critical path of every cross-chain settlement, so a revert in that dependency bricks the entire flow.**

I checked the places in Hyperbridge where this exact pattern could recur:

- `EvmHost.dispatchIncoming` / `dispatchTimeOut` (POST/GET delivery to `IApp`) — already isolated via low-level `.call` + `success` check, receipts rolled back on failure instead of reverting. [1](#0-0) 
- `HyperbridgeLzEndpoint.onAccept` — explicitly uses `try/catch` around the OApp's `lzReceive` for exactly this reason, with tests proving a reverting receiver doesn't brick the channel. [2](#0-1) 
- Substrate `request`/`response` handlers roll back the receipt on a failing `on_accept`/`on_response` rather than reverting the whole batch. [3](#0-2) 

These are all correctly hardened against the SponsorVault bug class. The one place where the same anti-pattern still exists is `IntentGatewayV2.fillOrder`, which makes an **unconditional, unguarded external call to the governance-configured `_params.priceOracle`** after every successful fill: [4](#0-3) 

```solidity
if (isSameChain) {
    _fillSameChain(order, options, commitment);
} else {
    _fillCrossChain(order, options, commitment);
}

if (_params.priceOracle != address(0)) {
    IIntentPriceOracle(_params.priceOracle)
        .recordSpread(commitment, order.source, order.inputs, options.outputs);
}
```

Inside `VWAPOracle.recordSpread` (the reference/only implementation), the call chain performs an **external call to `IERC20Metadata(outputToken).decimals()`**, where `outputToken` comes directly from the user-controlled `order.output.assets[i].token`: [5](#0-4) 

### Title
Unguarded external call to `IntentPriceOracle` in `fillOrder` reverts the entire (already-completed) settlement - (File: `evm/src/apps/IntentGatewayV2.sol`)

### Summary
`fillOrder` calls `IIntentPriceOracle(_params.priceOracle).recordSpread(...)` unconditionally, with no try/catch, after both `_fillSameChain` and `_fillCrossChain` have already moved real tokens to the beneficiary and, for cross-chain orders, already dispatched the `RedeemEscrow` message. Because Solidity reverts unwind the entire transaction, any revert inside the oracle call reverts the token transfers and the cross-chain dispatch that already logically completed, exactly mirroring the `SponsorVault` pattern: a single external dependency in the hot settlement path can deny the whole operation.

### Finding Description
`recordSpread` is reachable with attacker-influenced input: `outputToken` is taken from `order.output.assets[i].token`, which is chosen by whoever creates the order, not by the solver who is filling it. [6](#0-5)  `recordSpread` calls `IERC20Metadata(outputToken).decimals()` on that address without any static call safety, gas cap, or try/catch. A token contract that reverts on `decimals()` (or is simply not ERC-20, or is a non-contract address masquerading through arbitrary calldata), causes `recordSpread` to revert, which propagates straight up through `fillOrder` with no isolation. [7](#0-6) 

This is the same broken invariant as the SponsorVault report: the settlement path (`_fillCrossChain`/`_fillSameChain`, which already transferred tokens and dispatched the redemption message) is fully undone by the failure of a bolt-on accounting/analytics dependency that has nothing to do with the correctness of the fill itself.

### Impact Explanation
Every attempt to fill any order whose declared output token reverts on `decimals()` will fail atomically — the solver's token transfer and the cross-chain `RedeemEscrow` dispatch are rolled back together with the oracle call. This makes the order permanently unfillable via `fillOrder` for as long as `_params.priceOracle` is configured and non-zero, since `recordSpread` runs unconditionally on every successful fill path. The escrowed input tokens are not stolen, but the intended settlement is unauthorized-to-execute: the transaction that should have completed a legitimate transfer of value between two counter-parties (user and solver) cannot ever be mined successfully while the misbehaving oracle dependency remains wired in, unlike every other dispatch path in the codebase (`EvmHost.dispatchIncoming`, `HyperbridgeLzEndpoint.onAccept`), which were deliberately hardened against exactly this failure mode.

### Likelihood Explanation
The trigger requires no privileged actor: any user placing an order can pick an ERC-20-looking output token address whose `decimals()` call reverts (or a non-contract/self-destructed address at fill time), and any solver attempting to fill that specific order will always fail regardless of correctness of their fill. Because `IERC20Metadata(outputToken)` is invoked with the concrete `order.output.assets[i].token`, this is fully attacker-reachable without needing a malicious admin, relayer, or governance actor — the priceOracle itself does not need to be malicious, only the token address embedded in an order created by an ordinary user.

### Recommendation
Wrap the `recordSpread` call in a `try/catch` (mirroring the pattern already used in `HyperbridgeLzEndpoint.onAccept`), so a failure in price/analytics tracking never reverts a fill that has already legitimately transferred value and dispatched cross-chain messages:
```solidity
if (_params.priceOracle != address(0)) {
    try IIntentPriceOracle(_params.priceOracle)
        .recordSpread(commitment, order.source, order.inputs, options.outputs)
    {} catch {}
}
```

### Proof of Concept
1. User creates an `Order` whose `order.output.assets[0].token` is a deployed contract implementing `IERC20` transfer/approve semantics but reverting unconditionally on `decimals()` (e.g. a custom token, or a proxy pointing at an implementation without `decimals()`).
2. Solver calls `fillOrder(order, options)`. `_fillSameChain`/`_fillCrossChain` executes normally, transferring output tokens to the beneficiary (and, for cross-chain, dispatching the `RedeemEscrow` POST via the host).
3. Control reaches `IIntentPriceOracle(_params.priceOracle).recordSpread(...)` → `VWAPOracle.recordSpread` → `IERC20Metadata(outputToken).decimals()` reverts.
4. The revert propagates out of `fillOrder`; the entire transaction (token transfer + cross-chain dispatch) is rolled back.
5. Every subsequent attempt by any solver to fill this order reverts identically, making the order permanently unfillable through `fillOrder` while `_params.priceOracle` remains set.

Note: verifying the exact governance path that sets/updates `_params.priceOracle`, and whether `IntentGatewayV2Test.sol`/`VWAPOracleTest.sol` already cover a reverting-token scenario, would require further reading of those test files, which I did not have iterations left to fully inspect.

### Citations

**File:** evm/src/core/EvmHost.sol (L794-817)
```text
    function dispatchIncoming(PostRequest memory request, address relayer) external restrict(_hostParams.handler) {
        address destination = _bytesToAddress(request.to);
        uint256 size;
        assembly {
            size := extcodesize(destination)
        }
        if (size == 0) {
            // instead of reverting the entire batch, early return here.
            return;
        }

        // replay protection
        bytes32 commitment = request.hash();
        _requestReceipts[commitment] = relayer;

        (bool success,) = address(destination)
            .call(abi.encodeWithSelector(IApp.onAccept.selector, IncomingPostRequest(request, relayer)));

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L384-395)
```text
        // Deliver to the OApp. Isolate the external call so a deterministic revert (zero
        // recipient, over-cap mint, blocklisted recipient, malformed payload, paused OApp, etc.)
        // does not revert `onAccept`. On failure the payload is retained for later retry/recovery
        // via retryPayload/clear/skip/nilify/burn.
        Origin memory origin = Origin({srcEid: srcEid, sender: sender, nonce: nonce});
        try ILayerZeroReceiver(receiverAddr).lzReceive(origin, guid, message, address(0), "") {
            // delivered successfully
        } catch {
            bytes32 payloadHash = keccak256(abi.encode(guid, message));
            _inboundPayloadHashes[receiverAddr][srcEid][sender][nonce] = payloadHash;
            emit InboundPayloadStored(receiverAddr, srcEid, sender, nonce, payloadHash);
        }
```

**File:** modules/ismp/core/src/handlers/request.rs (L99-126)
```rust
		.map(|request| {
			let wrapped_req = Request::Post(request.clone());
			let mut lambda = || {
				let cb = router.module_for_id(request.to.clone())?;
				// Re-check the receipt right before dispatch. The up-front pass above
				// runs before any callback executes; a prior request's on_accept in
				// this same batch could have stored a receipt for this request
				// (directly or by re-entering the handler), and we must not invoke
				// on_accept a second time.
				if host.request_receipt(&wrapped_req).is_some() {
					Err(Error::DuplicateRequest { meta: wrapped_req.clone().into() })?
				}
				// Store request receipt to prevent reentrancy attack
				let signer = host.store_request_receipt(&wrapped_req, &msg.signer)?;
				let res = cb.on_accept(request.clone()).map(|weight| {
					total_weights.saturating_accrue(weight);

					let commitment = hash_request::<H>(&wrapped_req);
					Event::PostRequestHandled(RequestResponseHandled {
						commitment,
						relayer: signer,
					})
				});
				// Delete receipt if module callback failed so it can be timed out
				if res.is_err() {
					host.delete_request_receipt(&wrapped_req)?;
				}
				Ok(res)
```

**File:** evm/src/apps/IntentGatewayV2.sol (L413-452)
```text
    function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
        if (order.deadline < _blockNumber()) revert Expired();
        bytes32 commitment = keccak256(abi.encode(order));

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain && orderSource != currentChain) revert WrongChain();
        if (!isSameChain && orderDest != currentChain) revert WrongChain();

        if (_filled[commitment] != address(0)) revert Filled();

        if (_params.solverSelection) {
            bytes32 storedSelectionHash;
            assembly {
                storedSelectionHash := tload(commitment)
            }

            bytes32 expectedSelectionHash = keccak256(abi.encode(msg.sender, order.session));
            if (storedSelectionHash != expectedSelectionHash) revert Unauthorized();
        }

        uint256 outputsLen = order.output.assets.length;
        if (options.outputs.length != outputsLen) revert InvalidInput();
        if (order.inputs.length != outputsLen) revert InvalidInput();

        if (isSameChain) {
            _fillSameChain(order, options, commitment);
        } else {
            _fillCrossChain(order, options, commitment);
        }

        if (_params.priceOracle != address(0)) {
            IIntentPriceOracle(_params.priceOracle)
                .recordSpread(commitment, order.source, order.inputs, options.outputs);
        }
    }
```

**File:** evm/src/utils/VWAPOracle.sol (L170-198)
```text
    function recordSpread(
        bytes32 commitment,
        bytes memory sourceChain,
        TokenInfo[] calldata inputs,
        TokenInfo[] calldata outputs
    ) external restrict(_intentGateway) {
        // Validate inputs and outputs have the same length
        if (inputs.length != outputs.length || inputs.length == 0) {
            return;
        }

        bytes32 sourceChainHash = keccak256(sourceChain);
        uint256 tokensLen = inputs.length;
        for (uint256 i = 0; i < tokensLen; i++) {
            address inputToken = address(uint160(uint256(inputs[i].token)));
            address outputToken = address(uint160(uint256(outputs[i].token)));

            // Get decimals for input token from storage (remote chain)
            // Native tokens (address(0)) use 18 decimals
            uint8 inputDecimals = inputToken == address(0) ? 18 : _tokenDecimals[sourceChainHash][inputToken];
            if (inputDecimals == 0) continue; // Skip if decimals not configured

            // Get decimals for output token directly from contract (local chain)
            // Native tokens (address(0)) use 18 decimals
            uint8 outputDecimals = outputToken == address(0) ? 18 : IERC20Metadata(outputToken).decimals();

            // Normalize both amounts to 18 decimals for comparison
            uint256 inputAmountNormalized = _normalizeAmount(inputs[i].amount, inputDecimals);
            uint256 outputAmountNormalized = _normalizeAmount(outputs[i].amount, outputDecimals);
```
