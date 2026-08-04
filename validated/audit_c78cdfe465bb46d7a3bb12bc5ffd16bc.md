Based on the code I found, `_cancelFromDest` is called by `cancelOrder` on the destination chain with an arbitrary caller-supplied `Order` struct, and unlike `_cancelFromSource` it performs **no check that the order was ever actually placed/escrowed** on this chain before mutating state and dispatching a cross-chain message. [1](#0-0) 

### Title
`cancelOrder`/`_cancelFromDest` accepts unfilled, non-existent orders and dispatches a real `RefundEscrow` message - (File: evm/src/apps/intentsv2/ExtrinsicIntents.sol)

### Summary
`cancelOrder` computes `commitment = keccak256(abi.encode(order))` from a caller-supplied `Order` struct and only checks `_filled[commitment] != address(0)` before routing. [2](#0-1)  Because `_filled` is empty by default, this check does not distinguish between "order was previously filled" and "order was never placed at all." When `currentChain == orderDest`, `_cancelFromDest` is invoked directly on an arbitrary, unescrowed order.

### Finding Description
`_cancelFromDest` never verifies that `_orders[commitment][token]` holds any escrowed balance for the fabricated commitment — contrast this with `_cancelFromSource`, which explicitly requires `_orders[commitment][token] != 0` for every input token before proceeding. [3](#0-2)  `_cancelFromDest` instead unconditionally marks `_filled[commitment] = order.user` and dispatches a `RefundEscrow` `PostRequest` back to `order.source`, carrying attacker-chosen `order.inputs` (tokens/amounts) and `order.user` as beneficiary. [4](#0-3) 

This is the direct analog of the `voluntaryExit` report: a public function accepts an arbitrary "identifier" (there, a validator pubkey; here, a fabricated `Order`/commitment) that was never registered/deposited on this contract, yet the function proceeds to mutate state (`_filled`) and emit a real, protocol-meaningful cross-chain message, exactly mirroring the reported pattern of "no registry check before state change + signal emission."

On the receiving side, `onAccept` for `RefundEscrow` calls `_withdraw(body, true, true)`, which does gate on `_orders[commitment][token] != 0` per token (reverting `UnknownOrder` if the source-chain order doesn't actually exist for that commitment). [5](#0-4)  So a genuinely bogus commitment with nonzero token amounts is defeated on the source chain. However, `_cancelFromDest`'s local guard failure remains: `_filled[commitment]` gets permanently set on the destination chain for a commitment that was never escrowed there, without any escrow-existence check analogous to the one in `_cancelFromSource`. If an attacker can predict a legitimate future order's exact commitment (deterministic from `user`, `source`, `destination`, `nonce`, `inputs`, etc., all attacker/known values before the real order is placed), they can pre-mark `_filled[commitment]` on the destination chain, permanently blocking the legitimate solver's `fillOrder` call for that exact order (`if (_filled[commitment] != address(0)) revert Filled();` in `fillOrder`). This is a state/DoS corruption of the `_filled` mapping via an unvalidated, non-existent order — the same broken invariant as the external report (no registry of "does this identifier actually correspond to a live/escrowed object").

### Impact Explanation
The corrupted value is `_filled[commitment]` — a one-time commitment-finalization flag relied on for the "order not yet filled/cancelled" invariant across both `fillOrder` and `cancelOrder`. Setting it out-of-band for a not-yet-placed order pre-empts and permanently blocks the real order's execution path (denial of the solver's legitimate fill), and additionally causes a spurious `RefundEscrow` dispatch across Hyperbridge that will be rejected downstream (harmless there) but still consumes relayer fee/dispatch resources and emits a misleading protocol event on this chain.

### Likelihood Explanation
Exploitation requires the attacker to predict the exact future commitment hash of a not-yet-placed order (which depends on `order.nonce`, assigned monotonically in `placeOrder`, plus `user`/token/amount fields) and front-run the real order's arrival cross-chain — this is a narrow, timing-dependent condition, keeping likelihood moderate rather than trivial, but the missing guard itself is a clear code-level defect (`_cancelFromDest` lacks the escrow-existence check present in its sibling `_cancelFromSource`).

### Recommendation
Add the same guard used in `_cancelFromSource` to `_cancelFromDest`: require `_orders[commitment][token] != 0` for at least one/each of `order.inputs` tokens (or otherwise verify the order was actually escrowed/known on this chain) before setting `_filled[commitment]` and dispatching `RefundEscrow`.

### Proof of Concept
1. Observe the current `_nonce` value on the source-chain `IntentGatewayV2` and construct a candidate future `Order` struct with the exact fields (`user`, `source`, `destination`, `inputs`, `output`, `session`, `deadline`, `fees`, `nonce = current _nonce`) that a legitimate user is about to submit via `placeOrder` (nonce is public/predictable via `_nonce()`).
2. Compute `commitment = keccak256(abi.encode(order))` off-chain, matching what `placeOrder` will later compute.
3. On the destination chain, call `cancelOrder(order, options)` before the real order is filled; since `_filled[commitment] == address(0)`, `_cancelFromDest` executes, sets `_filled[commitment] = order.user`, and dispatches a `RefundEscrow` message to `order.source` — all without any escrow ever existing on the destination chain. [4](#0-3) 
4. When the legitimate solver later calls `fillOrder` for the real order with the same commitment on the destination chain, it reverts with `Filled()` because `_filled[commitment] != address(0)`, permanently denying the fill. [6](#0-5)

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L193-200)
```text
        uint256 inputsLen = order.inputs.length;
        for (uint256 i; i < inputsLen;) {
            if (_orders[commitment][address(uint160(uint256(order.inputs[i].token)))] == 0) revert UnknownOrder();

            unchecked {
                ++i;
            }
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-267)
```text
    function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.deadline >= _blockNumber()) {
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
        }

        _filled[commitment] = address(uint160(uint256(order.user)));

        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RefundEscrow)),
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
        );

        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L413-426)
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
```

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```
