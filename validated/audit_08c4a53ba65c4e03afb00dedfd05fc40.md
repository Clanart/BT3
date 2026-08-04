### Title
Malicious order creator can permanently block a solver's escrow redemption via a reverting input token, causing loss of the filler's earned funds - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2.withdraw()` iterates over every token escrowed for an order and pushes funds to a single `beneficiary` in one atomic loop with low-level `.call`s. If any single token transfer in that loop reverts, the whole function reverts — this is exactly the "shared loop, one bad receiver blocks everyone" primitive from the NFTX report, except here the "bad receiver" is a malicious *token* chosen by the order creator, and the party denied funds is the filler who already performed the cross-chain delivery.

### Finding Description
When a solver fills a cross-chain intent order, they become entitled to redeem the tokens the order creator escrowed on the source chain (`order.inputs`), which is settled via `RequestKind.RedeemEscrow` → `withdraw()`: [1](#0-0) 

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;

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
        unchecked { ++i; }
    }
    ...
```

`body.tokens` is derived directly from `order.inputs`, which the **order creator** (an unprivileged user) chooses freely when the order is created on the source chain. `beneficiary` for a `RedeemEscrow` is the solver/filler who delivered the outputs on the destination chain — a different, unprivileged party (`filler: msg.sender` recorded in the fill flow): [2](#0-1) 

Because the loop is atomic (a single `revert` unwinds every earlier successful transfer too, and there is no per-token isolation, retry-with-skip, or "store then let the beneficiary pull" fallback like NFTX's fix), an order creator can include a token contract in `order.inputs` that is engineered to always revert on `transfer` to the specific filler address (e.g., a blacklist-style ERC20, a token that reverts above a hard-coded destination address check, or one that simply always reverts). Since the filler is unknown until they claim the order, but is knowable/targetable once they call `fill()` (their address becomes public in the fill transaction, and the escrow redemption is not settled atomically with the fill — it requires a second, separate ISMP round trip back to the source chain), the order creator can construct or select such a token, wait for a solver to fill the order (paying out the destination-side outputs in good faith), and then have `withdraw()` permanently revert for that commitment on the source chain.

### Impact Explanation
This is a genuine loss-of-funds path for an unprivileged, legitimate party (the filler/solver), matching the bounty's "stealing or loss of funds" and "logic attacks" categories:
- The filler has already paid out real value on the destination chain to fulfill the order.
- The escrow redemption on the source chain (`withdraw`) can be permanently and unconditionally blocked by the token contract the order creator chose, with no code path to isolate the poisoned token and release the rest, and no way for the filler to "pull" their entitled tokens once the request commitment is jammed.
- `_filled[body.commitment]` is never set (the whole call reverts), so the commitment stays retryable forever but will fail identically every time — a permanent DoS/fund-lock rather than a transient one.

### Likelihood Explanation
Any user can create an order and choose arbitrary `order.inputs` token addresses (there is no allowlist enforced at order-creation time in the excerpts reviewed). Deploying a token that selectively or unconditionally reverts on `transfer` is trivial and entirely within the attacker's control, with no reliance on a compromised relayer, prover, or admin — satisfying the "unprivileged attacker, public entrypoint" requirement of the pivot.

### Recommendation
Do not require every token in an order's input set to transfer atomically to the same beneficiary in one call. Either:
- Isolate each token transfer in a try/catch (or low-level call ignoring individual failures) and credit failed transfers to a per-beneficiary claimable balance the filler can pull later (the exact mitigation the NFTX report recommended), or
- Validate/allowlist tokens usable in `order.inputs` at order-creation time so that only known-good, non-blacklisting ERC20s can be escrowed, or
- Track partial success/failure per token in `_orders` so a failing token doesn't block redemption of the others, and allow the beneficiary to retry only the failed leg.

### Proof of Concept
1. Attacker deploys `EvilToken`, an ERC20 whose `transfer()` reverts whenever `to == knownFillerAddress` (or simply always reverts after the order is filled, e.g. via an owner-toggled `paused` flag the attacker flips right after seeing the `fill` transaction in the mempool).
2. Attacker creates a cross-chain order with `order.inputs = [EvilToken, <normal token>]`, escrowing both on the source chain.
3. A solver observes the order, calls `fill()` on the destination chain, and delivers the requested outputs to the order's recipient — the solver has now paid real value.
4. The fill flow dispatches a `RedeemEscrow` request back to the source chain with `beneficiary = solver`.
5. When `onAccept` → `withdraw()` executes on the source chain, the loop reaches `EvilToken.transfer(solver, amount)`, which reverts; `withdraw()` reverts as a whole (`TransferFailed`), rolling back the "normal token" transfer as well.
6. The request is retryable but will revert identically every time — the solver's earned escrow (including the perfectly good normal token) is permanently locked, and the solver has no way to recover it. [3](#0-2)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L164-171)
```text
        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }

        emit OrderFilled({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: order.inputs});
    }
```
