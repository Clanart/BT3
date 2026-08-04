Found the analog. `onGetResponse` in `ExtrinsicIntents.sol` decodes `WithdrawalRequest` (containing `commitment`, `tokens`, and `beneficiary`) directly from `incoming.response.request.context` and passes it straight to `_withdraw`, with the *only* validation being that `incoming.response.values[0].value.length == 0` (the "unfilled" storage proof check). This is structurally the same class of bug as the flash-loan report: a callback that trusts the caller (`onlyHost`) but never checks that the payload it is acting on was actually the one *this contract itself* dispatched.

### Title
Unauthenticated `context` in `onGetResponse` allows draining escrow for orders not queried by `_cancelFromSource` - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
`onGetResponse` blindly trusts the `context` field carried on the verified GET response to decide *whose* escrow to release and *to whom*, without checking that `context` corresponds to the specific GET request this contract dispatched for that `commitment`/order.

### Finding Description
`_cancelFromSource` builds a `DispatchGet` whose `context` is `abi.encode(WithdrawalRequest{commitment, tokens: order.inputs, beneficiary: order.user})` [1](#0-0) . The `keys[0]` computed via `_calculateCommitmentSlotHash(commitment)` is what actually gets cryptographically proven against destination state — it is the only field independently verified by the Host/proof-verification pipeline.

`onGetResponse`, however, does not re-derive or check the `commitment`/`beneficiary`/`tokens` in `context` against the `keys` that were verified; it decodes `context` and calls `_withdraw` with whatever `WithdrawalRequest` is embedded there: [2](#0-1) 

The only guard is `msg.sender == host` (`onlyHost`) — precisely the same guard pattern flagged as insufficient in the flash-loan report (checking *who* called back, not *what* was originally requested). Since `context` is an arbitrary opaque `bytes` blob that is round-tripped through the Host's dispatch/response machinery, if it is not cryptographically bound to the proven storage key (i.e., the Host does not enforce that the same `context` submitted at `dispatch()` time is the one returned unmodified in the matching response, or if the `values[0].value.length == 0` check can be satisfied for a *different* order's key than the one described in `context`), an attacker can construct a response whose `context` names a *different* commitment/beneficiary/token set than the one whose storage slot was actually proven empty.

`_withdraw` itself only checks `_orders[commitment][token] != 0` before decrementing and transferring — it does not check that `msg.sender` initiated the corresponding cancel, nor that the `beneficiary` in `context` matches `order.user` for that specific `commitment`: [3](#0-2) 

### Impact Explanation
If the `context` bytes can be manipulated by anyone in the delivery/proof-generation path (a relayer building the GET response, or through any mismatch between the request that was proven and the request whose `context` is decoded), an attacker can redirect a legitimately-empty-fill-slot proof for one order to trigger `_withdraw` for an *arbitrary* other commitment/order and beneficiary of their choosing, draining another user's escrowed input tokens — a direct fund-theft / wrong-beneficiary primitive matching the bounty's "unauthorized transaction," "logic attack," and "false proof/state acceptance" categories.

### Likelihood Explanation
This requires that the `context` field is not strictly bound one-to-one with the proven `keys`/commitment in the underlying ISMP GET-response verification and dispatch/response matching logic (I could not fully verify this binding within the available EvmHost GET-response dispatch code in this scan — this is the key uncertainty). If the Host's GET-response delivery preserves and authenticates `context` as exactly what was set at dispatch time for that specific request commitment, then this reduces to a lower-severity defense-in-depth gap rather than an exploitable primitive. Given the review scope and time available, I was not able to trace the full GET-request/response commitment-binding logic in `EvmHost.sol` to conclusively confirm or rule out that `context` can diverge from the proven key.

### Recommendation
`onGetResponse` should re-derive the expected storage key/commitment from the decoded `WithdrawalRequest` and assert it matches `incoming.response.request.keys[0]` (i.e., recompute `_calculateCommitmentSlotHash(body.commitment)` and compare against the destination-instance-prefixed key that was actually proven), rather than trusting `context` as authoritative. Additionally, verify `_orders[body.commitment]` entries against `order.user`/`beneficiary` consistency before calling `_withdraw`, mirroring the remediation pattern from the source report: bind the callback's acted-upon data structurally to the specific request this contract itself dispatched, not merely to "some GET response the trusted Host delivered."

### Proof of Concept
Conceptual (could not fully construct on-chain PoC without confirming Host-side context binding):
1. User A places order A with commitment `C_A`, escrowing tokens, deadline passes.
2. User A calls `cancelOrder` → `_cancelFromSource` dispatches a GET request with `keys=[hash(C_A)]`, `context=WithdrawalRequest{commitment: C_A, beneficiary: A}`.
3. If the relaying/response-construction path does not cryptographically re-attach the exact original `context` to the exact proven `keys`, an attacker (or malicious/buggy relayer infra) submits a response proving `C_A`'s slot is empty but with `context` substituted to `WithdrawalRequest{commitment: C_B, beneficiary: attacker}` for a different, still-escrowed order B.
4. `onGetResponse` passes the length check (slot empty for `C_A`), decodes attacker's substituted `context`, and calls `_withdraw` for `C_B`, sending order B's escrowed tokens to the attacker.

Given I could not confirm within this scan whether `EvmHost`/the ISMP response-delivery path independently binds `context` to the specific request commitment it was dispatched under, this should be treated as requiring verification against `EvmHost.sol`'s GET dispatch/response commitment matching code before being confirmed as fully exploitable.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L202-221)
```text
        bytes memory context =
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}));

        bytes[] memory keys = new bytes[](1);
        keys[0] = bytes.concat(abi.encodePacked(_instance(order.destination)), _calculateCommitmentSlotHash(commitment));
        DispatchGet memory request = DispatchGet({
            dest: order.destination,
            keys: keys,
            timeout: 0,
            height: options.height,
            fee: options.relayerFee,
            context: context,
            payer: msg.sender
        });

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L319-324)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L391-410)
```text
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
