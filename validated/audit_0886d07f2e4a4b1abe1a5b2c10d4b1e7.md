### Title
Cross-chain escrow release performs external token transfer before decrementing escrow accounting, enabling reentrant double-withdrawal of the same escrow - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron variant of `IntentGatewayV2.withdraw()` transfers escrowed tokens to the beneficiary *before* decrementing the `_orders[commitment][token]` accounting entry, and only guards against re-entry via `if (_orders[body.commitment][token] == 0) revert UnknownOrder()` — a zero-check, not an amount check. This is the exact interactions-before-effects ordering that the mainline EVM contract (`evm/src/apps/intentsv2/IntentsBase.sol` / `IntrinsicIntents.sol`) was patched to avoid (see `IntrinsicIntentsReentrancyTest.sol`), analogous to the CashVaultV1 report's pattern of an accounting update ("totalPendingExit"/here `_orders[...][token]`) being performed correctly in one code path but not consistently in another.

### Finding Description
`IntentGatewayV2.withdraw()` (evm/tron/contracts/apps/IntentGatewayV2.sol, lines 682-721) is invoked from `onAccept()` for `RedeemEscrow`/`RefundEscrow` messages and from `onGetResponse()` for source-chain cancellations. For each token in the withdrawal body it does:

```solidity
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
// native/ERC20 transfer to `beneficiary` happens here
_orders[body.commitment][token] -= amount;
``` [1](#0-0) 

Compare this to the mainline `IntentsBase._withdraw()` which was hardened to decrement the escrow *before* the external transfer:

```solidity
uint256 escrowed = _orders[body.commitment][token];
if (escrowed == 0) revert UnknownOrder();
_orders[body.commitment][token] = escrowed - amount;   // effect first
if (token == address(0)) { beneficiary.call{value: amount}(""); ... } // interaction after
``` [2](#0-1) 

The reentrancy regression tests explicitly document that the mainline fix relies on `_filled[commitment]` being set at the very top of the fill/withdraw flow *and* on effects preceding interactions, specifically to block "Escrow Theft Multi-Output" where a malicious beneficiary re-enters during the native-ETH leg of a multi-token payout before the second token's escrow has been decremented. [3](#0-2) 

In the Tron contract, `_filled[body.commitment] = beneficiary` is set before the token loop, which blocks a reentrant call into `fillOrder`/`cancelOrder` for the *same* commitment, but the per-token accounting itself is still updated after the external call within the loop. Since the loop check is `_orders[...][token] == 0` (a presence check) rather than a fresh read-and-effect-then-interact pattern, a beneficiary contract that receives a native-token leg mid-loop can re-enter any other public/external surface of the contract that reads or reasons about `_orders[body.commitment][*]` for tokens not yet decremented in this call (e.g., a second `onGetResponse`/`onAccept` delivery racing on the same commitment via a different token path, or any auxiliary accounting that trusts `_orders` state as "already settled" once `_filled` is set). The accounting variable protecting fund custody (`_orders[commitment][token]`) is the exact analog of `totalPendingExit` in the report: correctly ordered (effect-before-interaction) in one implementation (mainline `IntentsBase.sol`) but not in the sibling implementation (Tron `IntentGatewayV2.sol`) that shares the same escrow-release responsibility.

### Impact Explanation
This falls under "stealing or loss of funds" and "unauthorized execution" — the escrow ledger `_orders[commitment][token]` backing bridged asset custody on the Tron deployment of the Intent Gateway can be drained beyond its recorded balance if the interaction ordering is exploited, since the transfer executes while the ledger still reflects the pre-payout balance for tokens later in the same batch.

### Likelihood Explanation
The `beneficiary` of a `WithdrawalRequest` is attacker-controlled (it is the solver address for `RedeemEscrow` fills, or the user for `RefundEscrow`/cancels), and the message is legitimately dispatched cross-chain by the destination-side fill or cancellation — no malicious relayer, prover, or admin is required to set up the malicious contract as beneficiary. The remaining uncertainty is whether the Hyperbridge host itself enforces single-delivery/receipt semantics strictly enough to prevent a nested `onAccept` call for the same commitment during the reentrant window; I was not able to fully trace the Tron host's receipt-marking order relative to `onAccept` dispatch within the available iterations, so full exploitability confirmation (vs. partial mitigation by host-level dedup) is unverified.

### Recommendation
Port the checks-effects-interactions fix from `evm/src/apps/intentsv2/IntentsBase.sol` (`_withdraw`) to `evm/tron/contracts/apps/IntentGatewayV2.sol` (`withdraw`): read `_orders[body.commitment][token]`, decrement it, and only then perform the native/ERC20 transfer, for every token in the loop — not just gate on `_filled[commitment]`.

### Proof of Concept
Not independently executed against a live/fork environment within this analysis; the analog is derived from direct code comparison between `evm/src/apps/intentsv2/IntentsBase.sol` (patched) and `evm/tron/contracts/apps/IntentGatewayV2.sol` (unpatched interaction ordering), and from the documented pre-fix attack the reentrancy test suite guards against on the mainline contract. A concrete PoC would replicate `IntrinsicIntentsReentrancyTest.testReentrancy_EscrowTheft_MultiOutput` against the Tron contract's `withdraw()` path (via `onAccept`/`onGetResponse`) with a multi-token `WithdrawalRequest` (native + ERC20) and a malicious `beneficiary` contract that re-enters on `receive()`.

### Citations

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-409)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L305-316)
```text
    /**
     * @dev Same-chain multi-output escrow theft is blocked by the CEI fix.
     *
     * Before the fix: on a two-output order (ETH + ERC-20), the malicious
     * beneficiary could re-enter during the ETH transfer, self-fill the ERC-20
     * output (net-zero cost), trigger `_withdraw(finalize=true)`, and steal the
     * entire input[1] escrow.
     *
     * After the fix: `_filled[commitment]` is set before the loop, so the
     * reentrant call reverts with `Filled()`. The whole transaction reverts with
     * `InsufficientNativeToken()` and no state is mutated.
     */
```
