## Finding [1](#0-0) 

The Hyperbridge IntentGateway (`IntentsBase._withdraw`) exhibits the same class of bug as the Reserve `M-16` finding: a single misbehaving asset in a multi-asset bundle permanently blocks release of every other (healthy) asset bundled with it, because there is no per-token withdrawal path and the whole release is one atomic loop.

### Title
Single misbehaving/blacklisting escrowed token permanently locks all other assets in an IntentGateway order - (`evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentsBase._withdraw` iterates over `body.tokens` (an order's full list of escrowed input tokens) and calls `IERC20(token).safeTransfer` for each one in a single atomic loop [2](#0-1) . This is invoked from `ExtrinsicIntents.onAccept` for both `RedeemEscrow` and `RefundEscrow` message kinds [3](#0-2) , and from `onGetResponse` for the cross-chain cancel path [4](#0-3) . There is no function anywhere in `IntentsBase`/`ExtrinsicIntents` that lets a beneficiary withdraw a subset of `body.tokens` individually — release is all tokens in one call, or nothing.

### Finding Description
An order's `inputs` array is an arbitrary, user-chosen list of ERC-20 tokens escrowed at `placeOrder` time. Just as the Reserve basket held multiple collateral assets where one bad asset (`cUSDP` self-destructing) blocked all `refresh()`-dependent functionality even though 99% of collateral was fine, an intent order can escrow multiple tokens where one becomes non-transferable after order placement but before settlement (e.g. blacklisted by its issuer for the beneficiary address, paused, upgraded to revert, or self-destructed).

When Hyperbridge later delivers the `RedeemEscrow`/`RefundEscrow` request, `EvmHost.dispatchIncoming` calls `onAccept` via a low-level `.call`; on failure it deletes the request receipt so delivery can be retried [5](#0-4) . But retrying re-delivers the exact same `WithdrawalRequest.tokens` array — the same bad token is always at the same index, so `_withdraw` will revert on that token every single time, forever. Since the loop is atomic, the storage decrements for every *other*, perfectly healthy token in the same commitment also roll back on each attempt, and there is no alternate code path to release just the good tokens. The `_orders[commitment][token]` escrow balances for the healthy tokens remain locked in the contract indefinitely — exactly the "99% of funds still exist but are permanently locked" scenario described in the source report.

The root cause mirrors the AssetRegistry pattern exactly: a batch/loop over heterogeneous external assets where any single asset's misbehavior reverts the whole batch, and no unregister/skip/individual-release mechanism exists to route around it.

### Impact Explanation
This causes genuine, permanent loss/lock of funds for either the order's user (on `RefundEscrow`) or the solver who already paid out the destination-chain output before the message was dispatched (on `RedeemEscrow`). Because the affected tokens are chosen at order-creation time by an ordinary, unprivileged user, and the "misbehaving" precondition (issuer-side blacklist/pause/upgrade/self-destruct) can occur independently of any bridge actor, this is a direct fund-loss/lock impact matching the bounty's accepted categories, without requiring a malicious relayer, prover, or admin.

### Likelihood Explanation
Requires only that (a) a user places (or a solver fills) an order whose `inputs`/`outputs` include more than one token, and (b) one of those tokens becomes non-transferable to the beneficiary address after settlement is initiated (blacklist, pause, contract upgrade, or self-destruct on the issuer side) — a realistic, externally-triggerable condition for real-world ERC-20s (USDC/USDT-style blacklisting, proxy upgrades, admin-key compromise on the token itself). No cooperation from Hyperbridge relayers, provers, or governance is needed.

### Recommendation
Make `_withdraw` resilient to a single failing token: wrap each per-token transfer in a try/catch (or low-level call) so a failure on one token does not roll back releases of the others, and persist any undelivered token amounts in a per-(commitment, token) "claimable" bucket that the beneficiary (or anyone) can retry/withdraw independently later, instead of bundling all tokens into one all-or-nothing atomic release.

### Proof of Concept
1. User places a cross-chain order with `inputs = [GOOD_TOKEN, BAD_TOKEN]`, escrowing both in the source-chain `IntentGateway`.
2. A solver fills the order on the destination chain and dispatches `RedeemEscrow` back to the source gateway with `WithdrawalRequest.tokens = [GOOD_TOKEN, BAD_TOKEN]` and `beneficiary = solver`.
3. Before delivery, `BAD_TOKEN`'s issuer blacklists the solver address (or pauses/upgrades/self-destructs the token) — `IERC20(BAD_TOKEN).transfer(solver, amount)` now always reverts.
4. Hyperbridge delivers the request; `EvmHost.dispatchIncoming` → `onAccept` → `_withdraw` loop reaches `BAD_TOKEN`, `safeTransfer` reverts, the whole `onAccept` call reverts, and `EvmHost` deletes the receipt for retry [6](#0-5) .
5. Every subsequent retry delivers the identical `WithdrawalRequest` and reverts at the same token — `GOOD_TOKEN`'s escrowed balance for this commitment is never released; the solver's `GOOD_TOKEN` payout is permanently stuck in the gateway contract with no alternate withdrawal path.

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-295)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L319-324)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
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
