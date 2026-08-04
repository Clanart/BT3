## Analysis

The BuyerAgent report's core broken invariant is: **a single unsanctioned/malicious asset embedded in a batch of otherwise-legitimate assets can permanently block redemption of the entire batch**, because the settlement code performs an unconditional loop of external asset transfers with no per-item isolation, so one failing transfer reverts release of everything else in the same call.

Hyperbridge's IntentGateway (`IntentsBase._withdraw`, mirrored in `evm/tron/contracts/apps/IntentGatewayV2.sol::withdraw`) has the exact same structural pattern in the escrow-release/refund path, and it sits directly in the fund-custody critical path.

### Title
Cross-chain order escrow permanently lockable via a single malicious input token in `_withdraw`'s all-or-nothing transfer loop - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentsBase._withdraw` (and its Tron equivalent `withdraw` in `evm/tron/contracts/apps/IntentGatewayV2.sol`) iterates over `body.tokens` and unconditionally transfers each token to the beneficiary in one loop with no per-token isolation. It is invoked from `onAccept` for both `RedeemEscrow` and `RefundEscrow`, and from `onGetResponse` for source-chain cancellation. An order creator can place a cross-chain order whose `order.inputs` include one token they can make permanently non-transferable (a custom ERC-20 they control, or by later revoking/blacklisting/pausing it). A solver fills the order and delivers real output on the destination chain; when the `RedeemEscrow` settlement message lands on the source chain, `_withdraw`'s loop reverts on the malicious token, which reverts the whole `onAccept` call and therefore never releases *any* of the escrowed tokens for that order — including the otherwise-good ones — to the solver.

### Finding Description [1](#0-0) 

`_withdraw` decrements `_orders[body.commitment][token]` and then transfers `amount` to `beneficiary` for every entry in `body.tokens`, with no `try/catch` around the transfer. If any single token transfer reverts (blacklist, pause, malicious/self-destructing token logic, or a token that only allows transfers under attacker-chosen conditions), the whole function — and thus the whole `onAccept`/`onGetResponse` call — reverts.

Critically, `EvmHost.dispatchIncoming` isolates a reverting `onAccept` at the *per-request* level (it swallows the revert and deletes the request receipt so the message "can be retried"): [2](#0-1) 

This isolation is designed to let a temporarily-failing delivery be retried later once conditions change — it is not designed to handle a request whose delivery is *permanently* impossible. Because `WithdrawalRequest.tokens` for a given order commitment is fixed at fill time (it's exactly `order.inputs`, see `ExtrinsicIntents.sol`/`IntrinsicIntents.sol` fill logic), retrying the identical `onAccept` call will fail identically forever if one of the bundled input tokens can never be transferred. There is no mechanism to redeem the other, perfectly good tokens in that same `WithdrawalRequest` independently — exactly the "batch purchase failure" class described in the external report, except here it blocks release of already-escrowed funds rather than blocking a purchase.

The onAccept dispatch path itself is: [3](#0-2) 

### Impact Explanation
This is fund loss/lock in the bridge's core custody path, not a generic DoS:
- The solver has already delivered real output tokens to the beneficiary on the destination chain (cross-chain fills are all-or-nothing, no partial fills) before the settlement message is even dispatched.
- If the order's `inputs` array contains one poisoned token, the solver can never redeem *any* of the escrowed inputs for that order — including the legitimate ones bundled alongside the poisoned token — because `_withdraw` has no per-token fault isolation and the request-level retry mechanism in `EvmHost.dispatchIncoming` cannot help when the failure is deterministic and permanent.
- The same defect applies to `RefundEscrow` (user cancellation) and to the `onGetResponse` cancel-from-source path, so a user's own legitimate refund can also be bricked if they (or anyone crafting on their behalf) included a token that later becomes non-transferable.
- This satisfies the bounty's "stealing or loss of funds" / fund-lock criterion, is triggerable by an unprivileged order creator, and requires no malicious relayer, prover, or admin.

### Likelihood Explanation
Likelihood is moderate-to-high: an attacker only needs to (a) deploy or select a token that can be made non-transferable at will (self-deployed malicious ERC-20, or a blacklist/pausable-capable stablecoin they can trigger against the eventual beneficiary), (b) include it as one of several `order.inputs` alongside a genuinely attractive token to induce a solver to fill, and (c) trigger the block condition before/at settlement time. `IntentFiller.verifyOrderOnSource` (`sdk/packages/simplex/src/core/filler.ts`) only checks that escrow balances are currently non-zero — it does not, and cannot, guarantee future transferability of every input token at redemption time.

### Recommendation
Make `_withdraw`'s per-token transfers fault-tolerant: wrap each token transfer in a low-level call/try-catch and, on failure, retain (rather than lose) the escrowed accounting for that specific token so it can be swept/retried independently, while still releasing all other tokens in the same `WithdrawalRequest`. Alternatively, disallow arbitrary/unvetted tokens in `order.inputs`, or split `RedeemEscrow`/`RefundEscrow` dispatch into one request per token so a stuck token cannot hold hostage the others.

### Proof of Concept
1. User places a cross-chain order via `placeOrder` with `order.inputs = [GoodToken: 1000 USDC, EvilToken: 1 wei]`, where `EvilToken` is an ERC-20 deployed by the user with a `transfer`/`transferFrom` that the deployer can permanently disable (e.g., an owner-toggleable `paused` flag, or a hardcoded blacklist on the expected solver address).
2. A solver observes the order, is attracted by the 1000 USDC value, and calls `fillOrder` on the destination chain, delivering the required output tokens to the user's beneficiary. This dispatches a `RedeemEscrow` `WithdrawalRequest{tokens: [GoodToken:1000, EvilToken:1]}` back to the source chain.
3. Before the settlement message is delivered, the user calls `EvilToken.pause()` (or equivalent) to permanently block transfers of `EvilToken` to the solver.
4. When the settlement message reaches the source-chain `IntentGatewayV2`, `EvmHost.dispatchIncoming` calls `onAccept` → `_withdraw`. The loop transfers `GoodToken` successfully, then reverts on `EvilToken.safeTransfer`, reverting the whole `onAccept` call.
5. `EvmHost.dispatchIncoming` catches the revert, deletes the request receipt, and returns — the message is "retriable" but will fail identically every time since `EvilToken` remains paused.
6. The solver, who already delivered real value on the destination chain, can never redeem the 1000 USDC (or the 1 wei of `EvilToken`) escrowed on the source chain — total, permanent fund lock triggered entirely by an unprivileged order creator. [4](#0-3) [5](#0-4)

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
