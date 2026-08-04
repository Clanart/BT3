### Title
Cross-chain `onAccept` for `RedeemEscrow`/`RefundEscrow` couples the one-time settlement marker with a multi-token transfer loop, letting a malicious order creator permanently lock a solver's escrowed reward - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
`IntentsBase._withdraw` (invoked from `ExtrinsicIntents.onAccept` / `onGetResponse` for `RedeemEscrow` and `RefundEscrow`, and mirrored in `IntrinsicIntents`/`IntentGatewayV2`) sets the one-time settlement flag `_filled[commitment] = beneficiary` and then, in the *same atomic call*, loops over every input token and transfers it out. [1](#0-0) 
If any single token in that loop reverts on transfer, the whole function — including the `_filled` write that already happened — is rolled back, exactly like Derby's `Vault.blacklistProtocol`, which combines the blacklist state write with a withdrawal that can revert and roll back the whole emergency action.

### Finding Description
`_withdraw` is the sole path by which escrowed order inputs are ever released, whether to a solver (`RedeemEscrow`, on order fill) or back to the user (`RefundEscrow`, on cancellation). It processes **all of an order's input tokens in one loop, in one call**: [2](#0-1) 
This is invoked unconditionally from `onAccept`, gated only by `onlyHost` + `_authenticate`: [3](#0-2) 

The order's `inputs` array — including the token addresses — is chosen entirely by the order creator (`order.user`) at placement time, not by the solver who later fills it. If a malicious user includes among their inputs a self-controlled, pausable/blacklistable ERC-20 alongside a legitimate token (e.g., WETH), a solver who fills the order delivers real value to the user on the destination chain, then a `RedeemEscrow` message is dispatched back to the source chain naming the solver as `beneficiary`. When that message lands and `_withdraw` runs, the loop first transfers the legitimate WETH, then hits the poisoned token and reverts (e.g., the malicious token's owner calls `pause()`/`blacklist(solverAddress)` moments before delivery). Because Solidity calls are atomic, the entire `_withdraw` call — including the WETH transfer that had already logically "succeeded" within the same call, and the `_filled[commitment]` settlement marker — is rolled back. The commitment is never marked filled, so the message can never be retried to a different outcome: every future delivery attempt hits the same poisoned token and reverts identically, permanently locking the solver's rightfully-earned WETH (and the frozen malicious token) in the contract with no recovery path other than governance-level `UpgradeContract`/`SweepDust` intervention.

This is structurally identical to the Derby `blacklistProtocol` bug: a state-changing action that is supposed to be resilient to a single failing asset instead ties its critical bookkeeping (there: the blacklist flag; here: the one-time `_filled` settlement flag and escrow accounting for *all* tokens in the order) to an atomic transfer that can be made to fail by an external/attacker-controlled asset, permanently blocking the intended outcome.

### Impact Explanation
An unprivileged user (order creator) can cause permanent loss of a solver's escrowed reward for a legitimately-filled order by including one poisonable token among the order's inputs. Because `_filled` never gets set, the escrow can neither be released to the solver nor otherwise recovered through the normal settlement path — this is a direct "loss of funds" impact reachable through a public entry point (`placeOrder` + normal `fillOrder`/cancel flow) with no admin, relayer, or prover compromise required.

### Likelihood Explanation
Moderate. It requires the attacker to author an order with a token they control (or a governance-pausable/blacklistable stablecoin they can influence) as one of several inputs, and to trigger the pause/blacklist just before delivery of the `RedeemEscrow`/`RefundEscrow` message. Solvers/relayers filling orders with multiple, unrelated-looking input tokens (one of which is attacker-deployed) would not obviously suspect the trap before filling.

### Recommendation
Decouple the critical settlement marker from per-token transfer success: set `_filled[commitment] = beneficiary` and finalize escrow accounting unconditionally, then attempt each token transfer independently (e.g., wrap each in a low-level call with `try/catch` or push failed transfers into a per-beneficiary claimable balance) so that a single poisoned/paused token cannot block settlement or lock unrelated, healthy tokens that belong to the same order.

### Proof of Concept
1. Attacker deploys `EvilToken`, an ERC-20 with an owner-controlled `pause(bool)` that makes `transfer` revert when paused.
2. Attacker calls `placeOrder` with `inputs = [WETH: 1e18, EvilToken: 1e18]`, offering an attractive output.
3. A solver calls `fillOrder` on the destination chain, delivering the requested outputs to the attacker (`order.user`).
4. `_fillCrossChain` dispatches `RedeemEscrow` back to source chain naming the solver as `beneficiary`.
5. Immediately before/while the Hyperbridge relayer delivers the message, attacker calls `EvilToken.pause(true)`.
6. `ExtrinsicIntents.onAccept` → `_withdraw` runs: WETH transfer would succeed but the loop then hits `EvilToken.safeTransfer` which reverts; the entire call, including `_filled[commitment] = solver`, reverts. [1](#0-0) 
7. Every retry of message delivery reproduces the same revert as long as `EvilToken` stays paused, permanently locking the solver's WETH (and the attacker's own EvilToken) in the gateway, with no way to mark the order filled or recover the WETH through the standard path.

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
