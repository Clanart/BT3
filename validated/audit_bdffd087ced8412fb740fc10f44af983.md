This confirms the mechanism. The `RedeemEscrow` message from `_fillCrossChain` sets `beneficiary: bytes32(uint256(uint160(msg.sender)))` — the solver's own address — with no restriction on `msg.sender` being a contract [1](#0-0) . On the source chain, `onAccept` routes `RedeemEscrow`/`RefundEscrow` straight into `_withdraw(body, ..., true)` with no try/catch around the native-token transfer [2](#0-1) , and `_withdraw` reverts the entire batch (all tokens for that commitment, including unrelated ERC-20s) if the native `.call` to the beneficiary fails [3](#0-2) .

Since `_filled[commitment]` on the destination chain is already set unconditionally at the top of `_fillCrossChain` before any of this [4](#0-3) , the order can never be re-filled or cancelled (the source-side cancel path checks that same destination slot via GET and reverts with `Filled()` if non-empty) [5](#0-4) , while the source-side escrow release keeps failing forever because the beneficiary contract deliberately rejects native ETH.

### Title
Malicious solver can permanently lock cross-chain escrow by choosing a self-address that rejects native-token settlement - (File: evm/src/apps/intentsv2/ExtrinsicIntents.sol)

### Summary
`_fillCrossChain` lets any solver set the `RedeemEscrow` beneficiary to `msg.sender` with zero restriction on that address's ability to receive native ETH. `_withdraw` in `IntentsBase.sol` reverts the whole escrow-release batch if a single native transfer fails, and this call is invoked from `onAccept` with no fallback path.

### Finding Description
In `ExtrinsicIntents._fillCrossChain`, `_filled[commitment] = msg.sender` is set unconditionally at the very start [4](#0-3) , then the solver pays the real output beneficiary and dispatches a `RedeemEscrow` `WithdrawalRequest` whose `beneficiary` field is `msg.sender` (the solver itself) [6](#0-5) . Nothing prevents `msg.sender` from being a contract with a `receive()` that always reverts.

On the source chain, this message is delivered via `onAccept`, which — for `RedeemEscrow`/`RefundEscrow` — calls `_withdraw(body, ..., true)` directly, with no try/catch isolating the transfer failure [2](#0-1) . Inside `_withdraw`, each token in `body.tokens` is released in a loop; for the native-token entry, a failed `.call{value: amount}("")` reverts the entire function via `revert InsufficientNativeToken()` [3](#0-2) . Because Solidity reverts unwind the whole call, any ERC-20 releases already processed earlier in the same loop are also rolled back — a single unrelated native-token beneficiary failure blocks release of the entire order's escrow, not just the native portion. This mirrors the H-3 pattern where a single reneging external call (there, `onERC721Received`; here, the native `.call`) blocks an entire settlement function that has no fallback/pull path.

Because `_filled[commitment]` on the destination chain was already set at the top of `_fillCrossChain` (before this problem manifests), the order is permanently marked filled there. The source-side cancellation path (`_cancelFromSource`/`onGetResponse`) checks that same destination `_filled` slot via a Hyperbridge GET and reverts with `Filled()` whenever it is non-empty [5](#0-4) , so the depositor can never fall back to cancellation once fill has been recorded on the destination. Meanwhile every relayer attempt to deliver the `RedeemEscrow` message keeps failing at the same native transfer, regardless of gas supplied, because the malicious contract's `receive()` is written to unconditionally revert.

### Impact Explanation
The depositor's escrowed input tokens (both the native-token entry and any co-escrowed ERC-20s for the same commitment) become permanently unrecoverable: the fill path can never finalize on the source chain, and the cancellation path is blocked because the destination already shows the order as filled. This is a direct loss/lock of user funds triggered by an unprivileged, ordinary solver — no relayer, prover, or admin collusion is required.

### Likelihood Explanation
Any address can act as a solver and call `fillOrder`/`_fillCrossChain` with `msg.sender` set to a purpose-built contract. The attacker only needs to front the real output tokens once (a bounded, possibly small cost, or even zero net loss if amounts are calibrated) to permanently grief a victim's escrow. No privileged role or race condition is needed — it is fully reachable through the public fill entrypoint.

### Recommendation
Wrap the native-token transfer path in `_withdraw` in a try/catch (or use a pull-based withdrawal pattern) so a beneficiary that cannot accept native ETH does not block release of other tokens or finalize the order in an unrecoverable state — analogous to the fix applied for the BvB `settleContract` bug, where the failing `safeTransferFrom` was wrapped so failure diverts to an escrow/claimable balance instead of reverting the whole settlement.

### Proof of Concept
1. Attacker deploys `MaliciousSolver` with `receive() external payable { revert(); }`.
2. Victim places a cross-chain `Order` on chain A with a native-token input escrowed via `IntentGatewayV2`/`ExtrinsicIntents`.
3. `MaliciousSolver` calls `fillOrder`, becoming `msg.sender` in `_fillCrossChain`; it pays the legitimate output beneficiary on chain B and the `RedeemEscrow` message is dispatched with `beneficiary = MaliciousSolver`.
4. On chain A, `onAccept` invokes `_withdraw`, which attempts `MaliciousSolver.call{value: amount}("")`; this always reverts, so `_withdraw` reverts with `InsufficientNativeToken()` on every relayer retry.
5. On chain B, `_filled[commitment]` is already set to `MaliciousSolver`, so `_cancelFromSource`'s GET check always finds the slot non-empty and reverts with `Filled()`.
6. The victim's escrowed native token (and any co-escrowed ERC-20 in the same order) is permanently stuck on chain A.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-92)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L140-147)
```text
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
        );
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-409)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
