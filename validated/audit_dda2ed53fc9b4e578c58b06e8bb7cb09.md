Confirmed: when `onAccept` reverts, `dispatchIncoming` swallows the revert, deletes `_requestReceipts[commitment]`, and returns — so the message is retried later, but with the exact same request payload and the exact same fee-bundling logic in `_withdraw`. If the failure condition is permanent (e.g., the fee token contract has a permanent compliance blacklist, or is paused for that beneficiary), retries can never succeed and the escrow is permanently stuck.

### Title
Bundled relayer/protocol fee transfer in `IntentsBase._withdraw` can permanently lock already-escrowed principal tokens - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`_withdraw()` — the single function that releases escrowed order inputs on both the cross-chain settlement path (`onAccept` → `RedeemEscrow`/`RefundEscrow`) and the cross-chain cancel path (`onGetResponse`) — combines two independent value transfers into one atomic call: (1) the escrowed principal input tokens to the beneficiary, and (2) the accumulated Hyperbridge `TRANSACTION_FEES` in the protocol fee token to the same beneficiary. Because both transfers happen in the same function and the fee transfer uses `safeTransfer` (which reverts on failure), any failure of the fee-token leg reverts the entire withdrawal, including the release of principal tokens that are already safely escrowed in the contract.

### Finding Description [1](#0-0) 

`_withdraw` releases all `body.tokens` (the principal escrow) in a loop, then, when `finalize` is true, performs one more `IERC20(feeToken()).safeTransfer(beneficiary, fees)` for the accumulated relayer/protocol fee. All of this happens in one atomic call with no isolation between the principal-release loop and the fee-release step.

This function is invoked from the trusted, unprivileged-triggerable cross-chain settlement path: [2](#0-1) 

`onAccept` is called by the host after authenticating the message, and unconditionally routes into `_withdraw`. If the host's low-level call into `onAccept` reverts (e.g., because the fee-token transfer inside `_withdraw` reverted), `EvmHost.dispatchIncoming` does not propagate the revert as a fatal error — it deletes the request receipt so the message can be retried: [3](#0-2) 

This "retry" semantics assumes the failure is transient. But the fee-token `safeTransfer` can fail for permanent reasons unrelated to the principal tokens being released:
- The fee token (a stablecoin such as USDC/USDT) blacklists the `beneficiary` address (compliance freeze) — a condition entirely outside the intent-gateway's or the solver's control, and one that never resolves on retry.
- The fee token contract is paused.
- The gateway's fee-token balance is depleted below the recorded `fees` amount for that commitment (e.g., due to another commitment's fee accounting or a prior `_sweepDust` draining balance below what commitments still expect), making the transfer permanently unsatisfiable until the balance is externally replenished.

In every one of these cases, the escrowed principal input tokens (already held safely by the gateway, unrelated to the fee-token balance) can never be released, because the same atomic function that would release them also attempts — and fails — the auxiliary fee payment. There is no independent code path to withdraw only the principal.

This is structurally identical to the reported `DIAWhitelistedStaking` bug class: a mandatory, coupled secondary payment (staking rewards / Hyperbridge relayer fee) blocks access to unrelated, already-custodied principal funds when that secondary payment cannot be completed.

### Impact Explanation
This causes permanent loss/lock of escrowed bridge funds (order.inputs) rightfully owed to the solver (on `RedeemEscrow`) or the user (on `RefundEscrow`/cancel), meeting the "loss of funds" / logic-attack impact bar for the bounty. Because settlement is a normal, unprivileged, permissionless flow (any properly filled/cancelled order eventually triggers `onAccept`), no malicious relayer, prover, or admin is required — the trigger is simply a beneficiary address that is blacklisted by the fee token, or a fee-token balance shortfall relative to accumulated `TRANSACTION_FEES` liabilities.

### Likelihood Explanation
Moderate. It requires either (a) a beneficiary/solver address that becomes blacklisted by the configured fee token (plausible for widely used stablecoins with compliance blacklists such as USDC/USDT), or (b) the gateway's feeToken balance falling short of the sum of all commitments' recorded `TRANSACTION_FEES` (an accounting edge case worth separately auditing, e.g., interactions with `_sweepDust`). Given Hyperbridge explicitly documents the fee token as "typically... stablecoins" [4](#0-3) , blacklist-capable stablecoins are a realistic and expected configuration, not a contrived edge case.

### Recommendation
Decouple the fee-token payout from the principal-token release in `_withdraw`: release the escrowed principal tokens unconditionally, and wrap the fee-token transfer in a `try/catch` (or use a non-reverting transfer pattern with a pull-based fee claim) so that a failure to pay the bundled Hyperbridge fee does not block release of the underlying escrow. If the fee transfer fails, retain the fee amount in a separate claimable balance for the beneficiary rather than reverting the whole settlement.

### Proof of Concept
1. Configure/observe a chain where the Hyperbridge `feeToken()` is a blacklist-capable stablecoin (e.g., USDC).
2. A solver fills a cross-chain order whose `beneficiary`/solver address later becomes blacklisted by the stablecoin issuer (or the gateway's feeToken balance is otherwise insufficient to cover the commitment's accumulated `TRANSACTION_FEES`).
3. The destination chain dispatches the `RedeemEscrow` message; when it lands on the source chain, `EvmHost.dispatchIncoming` calls `onAccept` → `_withdraw(body, false, true)`.
4. Inside `_withdraw`, the principal loop succeeds, but `IERC20(feeToken()).safeTransfer(beneficiary, fees)` reverts because the beneficiary is blacklisted.
5. The whole `onAccept` call reverts; `EvmHost.dispatchIncoming` catches this, deletes the request receipt, and the message is left to be retried — but retrying is futile since the blacklist condition never changes.
6. The solver's principal input tokens, though sitting untouched in the gateway contract, are now permanently unreachable through any code path.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-425)
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

        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }

            if (isRefund) {
                emit EscrowRefunded({commitment: body.commitment, tokens: body.tokens});
            } else {
                emit EscrowReleased({commitment: body.commitment, tokens: body.tokens});
            }
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

**File:** sdk/packages/core/contracts/interfaces/IDispatcher.sol (L109-116)
```text
    /**
     * @dev Returns the address of the ERC-20 fee token contract configured for this state machine.
     *
     * @notice Hyperbridge collects it's dispatch fees in the provided token denomination. This will typically be in stablecoins.
     *
     * @return feeToken - The ERC20 contract address for fees.
     */
    function feeToken() external view returns (address);
```
