## Title
Permanent lock of cross-chain intent escrow when the solver's address is blacklisted by the input token, with no recovery path — (`evm/src/apps/intentsv2/IntentsBase.sol`, `evm/src/apps/intentsv2/ExtrinsicIntents.sol`, `evm/src/core/EvmHost.sol`)

### Summary
`IntentGatewayV2`'s cross-chain intent flow settles input-token escrow by transferring directly to a single, immutable beneficiary address (the filling solver) baked into the `RedeemEscrow` message at fill time. If that solver's address is later blacklisted by the escrowed input token (e.g. USDC), the settlement transfer reverts every single time it is retried, and the source-chain cancellation path is also permanently blocked because the destination chain already recorded the order as filled. The escrowed funds become permanently stranded in the contract — the exact "any one participant blacklisted ⇒ funds locked forever" bug class from the external report, reproduced locally in the Intent Gateway's escrow-release logic.

### Finding Description
When a solver fills a cross-chain order, `ExtrinsicIntents.sol`'s fill function embeds the solver's address as the fixed beneficiary in a `WithdrawalRequest` and dispatches a `RedeemEscrow` POST request back to the source chain: [1](#0-0) 

On the source chain, the host's `dispatchIncoming` calls `onAccept`, which eventually reaches `IntentsBase._withdraw`, transferring each escrowed input token straight to that fixed beneficiary with no fallback address: [2](#0-1) 

If the token is a blacklist-enabled token (USDC-style) and the solver's address gets blacklisted (for any reason — sanctions, disgruntled counterparty self-blacklisting is out of scope, but third-party/regulatory blacklisting of a legitimate solver is in scope) `IERC20(token).safeTransfer(beneficiary, amount)` reverts unconditionally for that specific `beneficiary`.

`EvmHost.dispatchIncoming` treats a reverting `onAccept` as "retryable" by deleting the request receipt: [3](#0-2) 

But this retry mechanism is useless here: the `WithdrawalRequest.beneficiary` is fixed inside the already-hashed/committed request body dispatched from the destination chain — it cannot be changed on retry, so every future retry fails identically forever.

Worse, this is not a temporary hold — it is unrecoverable. The destination chain already set `_filled[commitment] = msg.sender` synchronously when the solver called `fillOrder` (confirmed by the cross-chain reentrancy test's own comments): [4](#0-3) 

The only cancellation path available after a fill is `_cancelFromSource`, which dispatches a Hyperbridge GET query to the destination's `_filled` storage slot and refunds only if that slot is empty: [5](#0-4) 

Since the destination slot is non-empty (correctly filled by the solver), `onGetResponse` will always revert with `Filled()` per the documented flow, so the user can never reclaim the escrow either: [6](#0-5) 

The result: the solver already delivered output tokens to the user (honoring their side of the trade), but can never receive the escrowed input tokens because the redemption transfer to their (now blacklisted) address will fail forever, and no other actor (user, protocol, governance) has any function to redirect or force-release that specific escrow to an alternate address. The full escrowed amount for that order commitment is permanently stuck in `IntentGatewayV2`.

### Impact Explanation
This is a genuine, unrecoverable loss of bridged/escrowed funds for the affected order — matching the bounty's "stealing or loss of funds" and "bandwidth/asset custody must move exactly once and only to the rightful beneficiary" criteria. The affected solver loses both the output tokens they already delivered and the input tokens they are owed, with no code path to recover them. Unlike the original Escrow.sol report (which locks all participants sharing one contract instance), here the blast radius is scoped to the specific order commitment, but the loss is total and permanent for that order and grows with every order the blacklisted solver has filled or fills before discovering the block.

### Likelihood Explanation
No malicious peer, relayer, prover, or governance actor is required. All it takes is a single legitimate solver address becoming blacklisted by a widely used stablecoin (USDC and similar tokens are exactly the class of token the Intent Gateway is designed to move), which is a routine, externally-triggered event with real-world precedent (OFAC sanctions, Tornado Cash association, etc.). Any order this solver has already filled cross-chain immediately becomes unrecoverable dead escrow.

### Recommendation
Do not perform a direct push-transfer to a beneficiary address baked immutably into the cross-chain message. Instead:
1. Credit escrow release as an internal claimable balance keyed by `(beneficiary, token)` rather than transferring immediately inside `onAccept`/`_withdraw`, and expose a separate `claim()`/`pull`-style withdrawal function the beneficiary calls themselves.
2. Allow the beneficiary (or the original solver, via a signed message) to register an alternate payout address before or after settlement, so a blacklisted address is not a dead end.
3. At minimum, add an escape-hatch/governance-gated `forceRefund`/`redirectBeneficiary` function scoped per-commitment so escrow is not permanently unrecoverable when a beneficiary transfer is provably and permanently failing.

### Proof of Concept
1. User places a cross-chain order on chain A with `inputs = [USDC amount X]`, destination chain B.
2. Solver `S` fills the order on chain B (`_fillCrossChain`), delivering output tokens to the user; `_filled[commitment] = S` is set on chain B immediately per the code path shown above.
3. `ExtrinsicIntents` dispatches a `RedeemEscrow` `WithdrawalRequest{beneficiary: S}` back to chain A.
4. Before (or after) delivery, Circle blacklists `S`'s address on USDC.
5. Chain A's host delivers the request; `onAccept` → `IntentsBase._withdraw` calls `IERC20(USDC).safeTransfer(S, X)`, which reverts because `S` is blacklisted.
6. `EvmHost.dispatchIncoming` deletes the request receipt "so it can be retried" — but every retry replays the same fixed `beneficiary = S` and fails identically forever.
7. User attempts `_cancelFromSource` after the deadline; the GET query to chain B's `_filled[commitment]` slot returns non-empty (`S`), so `onGetResponse` reverts with `Filled()` and refund is rejected.
8. The escrowed `X` USDC is now permanently stuck in the `IntentGatewayV2` contract on chain A with no code path to release it to `S` or refund the user.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L139-155)
```text
        address hostAddr = host();
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
        });
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L188-210)
```text
    function _cancelFromSource(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        if (options.height <= order.deadline) revert NotExpired();

        uint256 inputsLen = order.inputs.length;
        for (uint256 i; i < inputsLen;) {
            if (_orders[commitment][address(uint160(uint256(order.inputs[i].token)))] == 0) revert UnknownOrder();

            unchecked {
                ++i;
            }
        }

        bytes memory context =
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}));

        bytes[] memory keys = new bytes[](1);
        keys[0] = bytes.concat(abi.encodePacked(_instance(order.destination)), _calculateCommitmentSlotHash(commitment));
        DispatchGet memory request = DispatchGet({
            dest: order.destination,
            keys: keys,
            timeout: 0,
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

**File:** evm/src/core/EvmHost.sol (L794-818)
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
    }
```

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L396-413)
```text
    //
    // _fillCrossChain already applied the CEI pattern from the start (the
    // `_filled[commitment] = msg.sender` statement was never commented out in
    // ExtrinsicIntents.sol, unlike _fillSameChain). These tests confirm the
    // existing protection holds for single- and multi-output cross-chain orders.
    //
    // Setup difference from same-chain tests:
    //  - order.source = "EVM-2" (a remote chain, not the current host)
    //  - order.destination = host.host() (current chain — where fill happens)
    //  - No placeOrder needed; cross-chain fills don't access source-chain escrow.
    //
    // Attack flow (blocked):
    //   1. fillOrder routes to _fillCrossChain (source != dest, dest == current)
    //   2. _fillCrossChain sets _filled[commitment] = msg.sender immediately
    //   3. ETH output loop sends ETH to maliciousBeneficiary → receive() fires
    //   4. Reentrant fillOrder hits _filled[commitment] != 0 → Filled() revert
    //   5. Revert propagates through receive() → ETH .call returns false
    //   6. _fillCrossChain throws InsufficientNativeToken() → full tx rollback
```

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L63-63)
```text
**Cancel from source chain**: The user calls `cancelOrder()` on the source chain, which dispatches a `DispatchGet` storage read request to query the destination chain's fill status. The `CancelOptions.height` must be greater than `order.deadline` — this ensures the proof is taken from a block after the order has expired. A relayer processes this request on Hyperbridge by providing storage proofs from the destination chain. If the storage slot for `_filled[commitment]` is empty (order unfilled), Hyperbridge dispatches a response back to the source chain. The `onGetResponse` handler verifies the empty proof and calls `withdraw()` to refund the escrowed tokens to the user. If the order was filled, the response contains a non-empty value and the handler reverts with `Filled()`.
```
