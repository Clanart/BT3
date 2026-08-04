## Finding: Permanent Escrow Lock When Refund/Redeem Beneficiary Is a Blacklisted/Sanctioned Address - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary

The external report's core broken invariant is: **a payment/settlement path has no fallback when the designated recipient cannot legally or technically receive funds**, causing one party to permanently lose access to escrowed value. The `IntentGatewayV2` / `IntentGatewayV3` intent-settlement system in this repository has the same class of bug: `_withdraw()` in `evm/src/apps/intentsv2/IntentsBase.sol` unconditionally attempts to push escrowed tokens to a hard-coded `beneficiary` (either the order's `user` on refund, or the `msg.sender`/solver on fill), with no fallback if that transfer is unable to succeed (e.g. a blacklist-capable stablecoin such as USDC/USDT refuses the recipient). Unlike the same-chain path, the cross-chain destination-side cancellation path commits to blocking the order **before** the refund is confirmed deliverable, making the lock permanent and unrecoverable through any other on-chain path.

### Finding Description

`_withdraw()` transfers each escrowed token directly to `beneficiary` via `IERC20(token).safeTransfer(beneficiary, amount)`: [1](#0-0) 

`safeTransfer` reverts the entire transaction if the underlying ERC-20 `transfer` returns `false` or reverts — which is exactly the behavior of blacklist-enabled stablecoins (USDC, USDT) when the recipient address is sanctioned/blacklisted.

The cross-chain destination-cancel path commits state *before* this transfer can be verified to succeed: [2](#0-1) 

`_cancelFromDest` immediately sets `_filled[commitment] = order.user` on the destination chain (permanently blocking any future fill of the order) and then dispatches a `RefundEscrow` message to the source chain. On the source chain, `onAccept` authenticates the message and calls `_withdraw(body, true, true)`: [3](#0-2) 

If `order.user` (the beneficiary of the refund) is blacklisted for the escrowed token, `_withdraw`'s `safeTransfer` reverts every time this message is retried. Because Solidity reverts are atomic, the on-chain state itself is never corrupted mid-transaction — but the *outcome* is deterministic and permanent: the destination side has already locked the order as non-fillable (`_filled[commitment] = order.user`), and the source side can never release the escrow to any other address, because `WithdrawalRequest.beneficiary` is fixed to `order.user` with no alternate recipient, sweep, or governance recovery path defined anywhere in `IntentsBase.sol`, `ExtrinsicIntents.sol`, or `IntrinsicIntents.sol`. The identical pattern exists in the Tron/EVM V2 variant: [4](#0-3) 

The same holds symmetrically for `RedeemEscrow` (fill settlement): if the solver's address becomes blacklisted for the input token between fill and settlement, the escrowed input tokens intended for the solver become permanently stranded in the source-chain gateway contract, with the user having already received their output tokens on the destination chain — a strict analog of the report's "seller can seize the NFT while buyer's funds are never protected" pattern, just with input escrow instead of an NFT.

### Impact Explanation

This causes permanent, unrecoverable loss/lock of bridged escrow funds in the `IntentGateway` contracts — a production fund-custody path explicitly in scope ("Bridged assets, order escrow, refunds ... must move exactly once and only to the rightful beneficiary and amount" and "fund loss/lock" is an explicitly accepted impact). Once the destination-side `_filled` marker is set via `_cancelFromDest`/`_cancelFromSource`, there is no code path that lets the order be re-opened, redirected to a different beneficiary, or swept by governance — the tokens are stuck in the source `IntentGatewayV2`/`ExtrinsicIntents` contract indefinitely.

### Likelihood Explanation

No malicious relayer, prover, admin, or leaked key is required. The only precondition is that the order's `user` (for refunds) or the filling solver (for redemptions) is, or later becomes, an address that a blacklist-capable ERC-20 (USDC, USDT, and other centrally-administered stablecoins commonly used with intent-based bridging) refuses to accept — a realistic real-world event (OFAC sanctioning, exchange-triggered freezes) completely outside the protocol's control, exactly mirroring the report's threat model. Any user or relayer can trigger the destination-side cancellation path (`cancelOrder` is callable by anyone after `order.deadline`), permanently committing the lock once the beneficiary is unable to receive funds.

### Recommendation

Mirror the report's own remedy: when `_withdraw`'s transfer to `beneficiary` cannot succeed, do not let the failure be a bare, retried revert against a hard-coded beneficiary. Options:
- Add a fallback/pull-based withdrawal mechanism (e.g., escrow-to-claimable-balance pattern) so a different address, or a governance-controlled rescue path, can redirect funds if the primary beneficiary is provably unable to receive them.
- Decouple the destination-side `_filled` (fill-blocking) state transition from the guarantee of a successful source-side refund, or provide a governance/administrative override (analogous to `SweepDust`, but scoped to a specific stuck commitment) that can redirect an unrecoverable refund/redemption to an alternate address after a timeout, with appropriate authentication.
- At minimum, use a non-reverting transfer helper (try/catch) at the final settlement step so retries don't permanently wedge order state, and expose an explicit "stuck escrow" recovery entrypoint.

### Proof of Concept

1. User places a cross-chain order on the source chain escrowing USDC as `order.inputs`, with `order.user` set to an address `A`.
2. Before the order is filled, address `A` is added to USDC's on-chain blacklist (a real, independent event outside the protocol's control).
3. After `order.deadline`, anyone calls `cancelOrder(order, options)` on the destination chain, hitting `_cancelFromDest`: `_filled[commitment] = A` is set on the destination chain (blocking any future fill), and a `RefundEscrow` `DispatchPost` is sent to the source chain — see [2](#0-1) .
4. On the source chain, `onAccept` is triggered and calls `_withdraw(body, true, true)`, attempting `IERC20(usdc).safeTransfer(A, amount)` — see [1](#0-0) . USDC's blacklist causes `transfer` to revert, so `_withdraw` (and the whole `onAccept` call) reverts.
5. Every subsequent retry of the `RefundEscrow` message delivery reverts identically. The order can never be filled again (destination-side `_filled` is already set) and the escrowed USDC can never be released (source-side transfer always reverts). The funds are permanently locked in the `IntentGateway` contract with no recovery entrypoint in `IntentsBase.sol`, `ExtrinsicIntents.sol`, or `IntrinsicIntents.sol`.

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-259)
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }
```
