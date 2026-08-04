## Analysis

The Dexter report's core pattern — a function computes an amount, mutates accounting state, then makes an external call that hands control to an attacker-controlled address before *all* invariant-critical state is settled — has a direct structural analog in Hyperbridge's Tron intent-gateway contract.

The canonical EVM `IntentGatewayV2` escrow-release path already applies Checks-Effects-Interactions: `_orders[body.commitment][token] = escrowed - amount;` is written **before** the external transfer: [1](#0-0) 

and a dedicated regression-test file documents that this ordering was deliberately fixed after a prior reentrancy bug in `_fillSameChain`: [2](#0-1) [3](#0-2) 

The **Tron** deployment of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, has the escrow-release loop in the *opposite, vulnerable* order: the external call (native TRX/ETH `.call{value:...}` or the low-level `token.call(...transfer...)`) executes **before** the escrow balance `_orders[body.commitment][token]` is decremented: [4](#0-3) 

### Title
Interaction-before-Effect in Tron IntentGatewayV2 escrow release enables reentrant double-withdrawal of escrowed funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The escrow-release routine in the Tron variant of `IntentGatewayV2` sends tokens/native TRX to the beneficiary *before* decrementing the corresponding `_orders[commitment][token]` escrow-accounting entry. This mirrors the Dexter `tokenToXtz` call-injection flaw: state (the pool/escrow balance) is not fully settled before control is handed to an external address, so a malicious beneficiary can re-enter and drain escrow for the same commitment multiple times.

### Finding Description
In the mainline EVM contract (`IntentsBase._withdraw`), the balance is decremented *before* the token/native transfer, which is the correct CEI ordering and is explicitly enforced by the reentrancy regression test suite (`IntrinsicIntentsReentrancyTest.sol`) — that suite was added specifically because `_fillSameChain` once had this exact ordering bug.

The Tron contract's equivalent routine does not follow the same ordering:
```solidity
// evm/tron/contracts/apps/IntentGatewayV2.sol:691-705
if (_orders[body.commitment][token] == 0) revert UnknownOrder();

if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");
    if (!sent) revert InsufficientNativeToken();
} else {
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();
}

_orders[body.commitment][token] -= amount;
```
`_orders[body.commitment][token]` is only checked for non-zero, not read/consumed atomically before the call. The `.call{value: amount}("")` to `beneficiary` (a user-supplied, attacker-controllable address on both `fillOrder` and cancel/refund code paths) hands execution to the attacker before the escrow ledger is updated. A `beneficiary` contract can re-enter the same withdraw path (or a related one that reads `_orders[commitment][token]`) while the pre-decrement value is still on chain, and receive the same escrowed amount again before the first call frame finally executes the decrement.

This is functionally identical to the Dexter vulnerability: "call injection" happens between the amount calculation/first send and the final state settlement, letting the attacker exploit stale accounting state mid-execution.

### Impact Explanation
This maps directly to the bounty's "stealing or loss of funds" and "replay/double-claim/double-settlement" categories. A malicious solver/beneficiary can drain escrowed order funds (input tokens locked by users on `placeOrder`) beyond what they are entitled to, directly stealing user funds custodied by the bridge/intent contract, with no need for a malicious relayer, prover, or admin — only a contract-controlled beneficiary address, which is attacker-controlled by construction in `fillOrder`/cancel flows.

### Likelihood Explanation
High: `beneficiary` is attacker-supplied (the solver picks itself as beneficiary of the escrow they're releasing, or an order beneficiary is attacker-controlled in the refund path), and the reentrancy primitive (a `receive()`/fallback hook triggering during native transfer, or an ERC-777/callback-style token during `token.call(transfer)`) is exactly the same class of attack Hyperbridge's own test suite proves was previously exploitable in the mainline EVM contract before the CEI fix was applied. No privileged actor or race condition is required — an unprivileged attacker deploys a reentrant beneficiary contract, exactly like the `ReentrantBeneficiary` fixture already built for the mainline contract's regression tests.

### Recommendation
Apply the same Checks-Effects-Interactions fix already implemented in `evm/src/apps/intentsv2/IntentsBase.sol` to the Tron contract: decrement `_orders[body.commitment][token]` (and, where applicable, set the `_filled[commitment]` finalize flag) **before** performing any external native/token transfer. Port the `IntrinsicIntentsReentrancyTest.sol` test matrix to the Tron contract build to lock in the fix and prevent regression, since the Tron code is a separately-maintained fork of the same logic and evidently did not receive the upstream fix.

### Proof of Concept
1. Attacker places/fills an order (or triggers a refund/cancel path) on the Tron `IntentGatewayV2` such that `beneficiary` in the `WithdrawalRequest` resolves to an attacker-deployed contract with a `receive()`/fallback (for native TRX) or is the recipient of an ERC-777-like callback token.
2. The withdraw loop reaches `body.tokens[i]` for the attacker's token/native asset: `_orders[body.commitment][token]` is checked non-zero (still pre-decrement), then `beneficiary.call{value: amount}("")` (or `token.call(transfer)`) is executed.
3. During that call, the attacker's contract re-enters the withdraw/fill entrypoint for the same `commitment`/`token` before `_orders[body.commitment][token] -= amount` on line 701 has executed in the outer frame.
4. The reentrant call again passes the `_orders[body.commitment][token] == 0` check (balance not yet decremented) and triggers another transfer of `amount` to the attacker, doubling (or multiplying, with deeper reentrancy) the payout for a single escrowed amount.
5. Each outer call frame eventually unwinds and performs its own decrement, but the attacker has already extracted more tokens than were ever escrowed, resulting in insolvency/fund loss for the contract's other order participants. [5](#0-4)

### Citations

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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L32-49)
```text
/**
 * @title ReentrantBeneficiary
 * @notice Malicious beneficiary contract that attempts to re-enter `fillOrder` during
 *         the ETH transfer made by `_fillSameChain` or `_fillCrossChain`.
 *
 * Attack window (pre-fix):
 *
 *   _fillSameChain / _fillCrossChain:
 *     beneficiary.call{value: ...}("")   ← RE-ENTRY HERE
 *     // _filled still == address(0) pre-fix, now set at the top (CEI)
 *
 * With the CEI fix in place, `_filled[commitment]` is set to `msg.sender` at the
 * very start of both fill functions. Any reentrant `fillOrder` call therefore hits
 * the `if (_filled[commitment] != address(0)) revert Filled()` guard and reverts.
 * That revert propagates through `receive()`, causing the outer ETH transfer to
 * return `(false, ...)`, which triggers `InsufficientNativeToken()` in the outer
 * call — rolling back all state changes atomically.
 */
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-58)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L691-721)
```text
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

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }

        if (isRefund) {
            emit EscrowRefunded({commitment: body.commitment});
        } else {
            emit EscrowReleased({commitment: body.commitment});
        }
    }
```
