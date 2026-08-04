### Title
Malicious order input token can permanently block escrow release, freezing solver-owed funds - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2.withdraw()` releases escrowed order-input tokens to a beneficiary using a raw `.call` to `transfer()` and reverts the entire request if the call fails. An order creator fully controls which token contracts are listed as `order.inputs`, so a malicious user can escrow a token whose `transfer()` succeeds only when the destination is the user themselves and reverts for any other recipient. This mirrors the seed report's pattern: an attacker-chosen parameter (there, `auctioneer=address(0)`; here, a hostile input token) makes the fund-release step permanently un-executable, freezing counterparty value.

### Finding Description
`withdraw()` is the internal settlement primitive invoked from `onAccept()` for both `RedeemEscrow` (solver payout after a destination fill) and `RefundEscrow` (cancellation refund) messages: [1](#0-0) 

For each escrowed token it does:
```
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
```
`order.inputs[i].token` is fully attacker-supplied at `placeOrder()` time — nothing in the gateway restricts input tokens to an allowlist or vets their transfer behavior. A user can therefore escrow a custom ERC-20 whose `transfer()` function reverts unless `msg.sender == deployer` or the recipient is a hardcoded address (functionally identical to the seed report's "revert on transfer to certain address" primitive, generalized to "revert on transfer to anyone but me").

Flow of the attack:
1. Attacker places a cross-chain order (`placeOrder`) escrowing their hostile token as `order.inputs`, offering an attractive `output` to lure a solver.
2. A solver, on the destination chain, delivers the required output tokens to the order's `beneficiary` (the attacker) via `fillOrder` — real value leaves the solver's hands here.
3. The destination gateway dispatches a `RedeemEscrow` POST request back to the source chain instructing `withdraw()` to pay the solver from escrow.
4. On the source chain, `onAccept()` → `withdraw()` attempts `token.call(transfer(solver, amount))`, which the hostile token contract reverts on (since the recipient is the solver, not the attacker).
5. `withdraw()` reverts with `TransferFailed()`, so the whole `onAccept()` call reverts. Nothing about the escrowed input's control conditions changes between retries, so this failure is permanent and deterministic — the message can be resubmitted indefinitely and will always revert.

The solver has already paid the output tokens on the destination chain but can never redeem the escrowed input on the source chain. This is functionally identical to the audited Yield bug: the victim (here, the solver/protocol) is forced to eat a loss because a value-controlling actor engineered an unconditionally-failing transfer to the correct beneficiary while still allowing transfers back to themselves (e.g., via `RefundEscrow`, whose `beneficiary` the docs note is hardcoded to `order.user` — the attacker — so a cancellation-path refund to themselves would still succeed, letting them reclaim the same tokens while leaving the solver's `RedeemEscrow` claim permanently blocked).

### Impact Explanation
This breaks the "moves exactly once and only to the rightful beneficiary" invariant for order escrow: an attacker can guarantee that a legitimate settlement participant (the solver, and by extension the protocol) never receives owed value, while retaining the ability to recover the same escrow themselves via the refund path. Solvers filling such orders sustain guaranteed value loss with no recourse, since the ISMP request cannot be timed out cleanly to correct this (the failure happens inside `onAccept`, not the timeout path) and any retry hits the identical revert.

### Likelihood Explanation
Fully reachable by an unprivileged EOA using the public `placeOrder()` entrypoint, no relayer, prover, or admin cooperation is required, and no front-running is necessary — the malicious token is chosen and deployed ahead of time by the order creator. The only "cost" to the attacker is designing a simple ERC-20 with conditional-revert `transfer()` logic, which is trivial and requires no unusual chain conditions.

### Recommendation
Do not let `withdraw()` unconditionally revert the escrow-release path based on the arbitrary input token's behavior:
- Use `SafeERC20.safeTransfer` combined with a try/catch pattern (or a pull-based claim design) so that a reverting/misbehaving input token cannot block delivery of receipts and cannot block release of *other* tokens in the same order.
- Consider maintaining a per-commitment "stuck" accounting so that if the intended transfer fails, the amount is recorded as claimable and the request/receipt is still marked settled (preventing indefinite retries against a deterministic revert), with a governance or permissionless sweep/escape hatch for stuck balances.
- Alternatively, restrict `order.inputs` to a vetted token allowlist, closing the arbitrary-token attack surface entirely.

### Proof of Concept
1. Attacker deploys `EvilToken`, an ERC-20 whose `transfer(to, amount)` succeeds only if `to == attacker`, otherwise reverts.
2. Attacker calls `placeOrder` with `order.inputs = [{token: EvilToken, amount: X}]`, `order.output` = attractive real tokens, `order.user = attacker`.
3. A solver calls `fillOrder` on the destination chain, sending real output tokens to `attacker` (per `PaymentInfo.beneficiary = order.user`), matching: [2](#0-1) 
4. The destination gateway dispatches `RedeemEscrow` with `beneficiary = solver`.
5. On the source chain, `onAccept` calls `withdraw(body, false)`: [3](#0-2) 
`token.call(transfer(solver, X))` reverts inside `EvilToken`, causing `withdraw` to `revert TransferFailed()` — permanently, on every resubmission.
6. Attacker still has the freedom to invoke a destination-side cancellation (`RefundEscrow`, `beneficiary` hardcoded to `order.user = attacker`) if the order were not yet marked filled, or otherwise retains control over their own escrowed tokens, while the solver's payout claim never completes — the solver has irreversibly lost the output tokens sent in step 3.

Note: This finding is derived purely from static code review of `IntentGatewayV2.sol`/`IntrinsicIntents.sol`; I did not execute a live Foundry test to observe the exact end-state accounting (e.g., whether the escrowed EvilToken balance remains locked in the contract vs. becomes double-claimable through a different function), so the precise downstream bookkeeping after the permanent revert should be verified with a dedicated PoC test before remediation.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-699)
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
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L99-111)
```text
            uint256 beneficiaryTotal = fillAmount + beneficiaryShare;

            if (token == address(0)) {
                if (msgValue < beneficiaryTotal + protocolShare) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransferFrom(msg.sender, beneficiary, beneficiaryTotal);
                if (protocolShare > 0) {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), protocolShare);
                }
            }
```
