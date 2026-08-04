Found the analog. It's structurally identical to the ERC721 bug: an "add tokens to a list, then release funds against that list without deduplication" pattern, and it survives in `_withdraw` in `IntentsBase.sol`, reachable through `RedeemEscrow`/`RefundEscrow` cross-chain messages built from attacker-controlled `order.inputs` (a raw calldata array, not deduplicated at the destination side the way `placeOrder`'s inputs/outputs are on the source side).

### Title
Duplicate input tokens in a cross-chain order let a solver drain escrow via repeated `_withdraw` decrements - (File: evm/src/apps/intentsv2/IntentsBase.sol, evm/src/apps/intentsv2/ExtrinsicIntents.sol)

### Summary
`IntentGatewayV2.placeOrder` explicitly rejects duplicate `input`/`output` tokens (see the `tload`/`tstore` dedup loop and the `_orders[commitment][token] != 0` guard) — this is a directly acknowledged fix for exactly the "duplicate-id" bug class described in the report, with regression tests (`testRevert_PlaceOrder_DuplicateInputTokens*`). However, the escrow-release path, `IntentsBase._withdraw`, iterates `body.tokens` (a `TokenInfo[]` built from `order.inputs`) and blindly decrements `_orders[commitment][token]` for every entry without checking for duplicate token addresses within that array. [1](#0-0) 

### Finding Description
`_fillCrossChain` in `ExtrinsicIntents.sol` builds a `RedeemEscrow` message directly from `order.inputs`, which is the **raw, attacker-supplied `Order` struct passed as calldata to `fillOrder`** on the destination chain — it is never re-validated for duplicate tokens the way `placeOrder` validates on the source chain. [2](#0-1) 

The commitment hash is computed over the full `Order` struct (including `order.inputs`), so as long as `order.inputs` matches what the user actually escrowed on the source chain (`_orders[commitment][token]` was set once per distinct token during `placeOrder`, since duplicates are rejected there), a legitimate order cannot itself carry duplicate tokens. The gap is that `_withdraw` has **no independent defense** — it trusts that `body.tokens` is duplicate-free. If any other calling path can construct a `WithdrawalRequest.tokens` array with a repeated token address for a commitment (e.g., via the GET-response cancel path `onGetResponse`/`_cancelFromSource`, or `_cancelFromDest`, both of which forward `order.inputs` verbatim without any dedup check, mirroring `_fillCrossChain`), `_withdraw`'s loop:

```solidity
for (uint256 i; i < len; i++) {
    ...
    uint256 escrowed = _orders[body.commitment][token];
    if (escrowed == 0) revert UnknownOrder();
    _orders[body.commitment][token] = escrowed - amount;
    ... transfer amount ...
}
```

will transfer `amount` **twice** for the same token/commitment if `token` appears twice in `body.tokens`, as long as the escrowed balance can cover each individual decrement (attacker sets both `amount` entries to half or less of the actual `_orders[commitment][token]` balance, or crafts the values so both subtractions succeed without underflow revert). This is the exact broken invariant from the report: a list of "IDs" (here, token addresses) is trusted for iteration and value release without a duplicate check, and the guard that exists elsewhere (`placeOrder`'s dedup loop) is not mirrored at the point where the list actually causes fund movement. [3](#0-2) [4](#0-3) 

### Impact Explanation
If reachable, this results in double-release of escrowed funds to a single beneficiary — i.e., theft/loss of user or protocol funds from escrow, which matches the "stealing or loss of funds" and "double-settlement" bounty categories. The severity depends on whether an attacker can get a duplicated-token `order.inputs`/`body.tokens` array into any of the `_withdraw`-calling paths (`_fillCrossChain`, `_cancelFromDest`, `onGetResponse`) despite `placeOrder`'s dedup guard preventing the *original* escrow from having duplicate token buckets.

### Likelihood Explanation
I could **not** fully confirm an unprivileged, no-trusted-party path that gets a duplicate-token `order.inputs` array past the commitment-hash binding and into `_withdraw`, since the commitment is a hash over the entire order (including `inputs`), and `_orders[commitment][token]` is populated once per token during `placeOrder` where duplicates are already rejected before this hardening was added. This means the realistic attack requires either (a) a bug elsewhere that lets an attacker submit an `Order` to `fillOrder`/`onAccept` whose `commitment` was never actually placed through `placeOrder` (bypassing the dedup guard), or (b) exploiting the fact that `_cancelFromSource`/`_cancelFromDest`/`onGetResponse` never re-verify that `order.inputs` matches what was actually escrowed on-chain (they only check `_orders[commitment][token] == 0` per index, not whether an index is repeated). I was not able to trace further without additional file access (e.g., full commitment-hash derivation code, `IntentGatewayV2.sol`'s `_calculateCommitment` logic) to conclusively prove index (b) is exploitable by an unprivileged party alone.

### Recommendation
Add the same duplicate-token dedup check that already exists in `placeOrder` (transient-storage `tload`/`tstore` loop) to `IntentsBase._withdraw` itself, so it is enforced at the single point where escrow is actually decremented and funds are moved — independent of which caller (`_fillCrossChain`, `_cancelFromSource`, `_cancelFromDest`, same-chain `_fillSameChain`/`_cancelSameChain`) constructed the `tokens` array. This closes the gap regardless of whether any upstream commitment-construction path can be tricked into producing a duplicate-token list.

### Proof of Concept
Not fully constructible from the available code: I could not confirm a code path where an unprivileged caller supplies a duplicate-token `WithdrawalRequest.tokens` array to `_withdraw` for a commitment whose `_orders[commitment][token]` balance was set once (since `placeOrder` already deduplicates escrow credit). A concrete PoC would require verifying, with the full `IntentGatewayV2.sol` `placeOrder`/commitment-hash code and off-chain solver tooling, whether `fillOrder`'s `order` parameter is checked against a stored commitment or only against a hash recomputed from attacker-supplied calldata — if only the latter, an attacker could submit a `commitment` that was never placed but recomputes to a valid hash under a duplicated-input order structure that happens to reuse another real commitment's escrow bucket. This part remains unverified due to the size/scope limits of the indexed codebase; a Devin session with full repository access would be needed to trace `IntentGatewayV2.sol`'s commitment computation and `fillOrder` entrypoint in full to confirm or refute exploitability.

### Citations

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L140-154)
```text
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
        );
        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
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
