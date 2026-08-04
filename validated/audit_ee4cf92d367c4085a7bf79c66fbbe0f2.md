No `nonReentrant`/`ReentrancyGuard` modifier exists anywhere in the Tron `IntentGatewayV2.sol` contract, confirming the finding below.

### Title
Checks-Effects-Interactions violation in `withdraw()` allows reentrant double-drain of multi-asset escrow - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The FeeAuction report's core defect is: a payout function transfers assets based on stale/unsynchronized state and only updates its bookkeeping *after* the transfer, so a second caller (or a reentrant path) can extract value that has already logically been spent. The exact same broken invariant — "pay out, then update the accounting" instead of "update the accounting, then pay out" — is reproduced locally in the Tron deployment's `withdraw()` function, which is the cross-chain escrow-release path for `IntentGatewayV2`.

### Finding Description
`withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` is the internal handler invoked by `onAccept()` (for `RedeemEscrow`/`RefundEscrow` messages) and by `onGetResponse()` (for source-chain cancellations). It iterates over `body.tokens` and, per token: [1](#0-0) 

1. Checks `_orders[body.commitment][token] == 0` and reverts if so.
2. **Immediately performs the external transfer** — either a raw native `.call{value: amount}("")` to `beneficiary`, or a low-level ERC-20 `.call(transfer(...))`.
3. **Only after** the transfer completes does it decrement `_orders[body.commitment][token] -= amount`.

Compare this to the audited main-line EVM implementation of the same logic, `_withdraw()` in `evm/src/apps/intentsv2/IntentsBase.sol`, which does the opposite — it decrements the escrow mapping *before* making the external call: [2](#0-1) 

Because `beneficiary` for `RedeemEscrow` is the solver who filled the order on the destination chain — an attacker-controlled address by design — and because the withdrawal loop can carry **multiple token entries in a single `WithdrawalRequest.tokens` array** (e.g., native ETH plus one or more ERC-20s escrowed for the same order), the native-token `.call` in the Tron `withdraw()` hands control to attacker code *while the accounting for every token entry after the current one in the loop is still unmodified*. The zero-balance guard (`_orders[...] == 0`) is the only thing standing between "already paid" and "payable again," and it is checked once per token at the top of each loop iteration, not re-verified after each external call.

No `nonReentrant` guard exists anywhere in this file, unlike the equivalent `placeOrder` on the mainline EVM contract which is `nonReentrant`.

### Impact Explanation
This is a direct instance of the "bridged assets ... must move exactly once and only to the rightful beneficiary and amount" pivot. If a solver constructs an order whose escrowed inputs include a native-token entry ordered before one or more ERC-20 entries in `body.tokens`, filling that order as a malicious contract lets the solver's `receive()`/fallback re-enter any externally reachable function that can trigger another `withdraw()` invocation *reading the not-yet-decremented later entries* in the same commitment's escrow map, extracting more value than the order actually escrowed. This is loss of bridged/escrowed funds and unauthorized extraction beyond the rightful amount — squarely in-scope impact categories (stealing/loss of funds, transaction manipulation).

### Likelihood Explanation
Likelihood is contingent on finding a synchronously reachable reentry point (this Tron contract's `onAccept`/`onGetResponse` are `onlyHost`-gated, so an attacker cannot trivially re-invoke `withdraw()` itself mid-callback without the host permitting reentrant dispatch). This is the one open verification gap in this analysis: I could not fully trace the Tron host's dispatch code path to confirm or rule out a synchronous double-invocation of `onAccept`/`onGetResponse` for the same or a different commitment within one relayer submission. Independent of that specific reentry vector, the code is unconditionally a CEI violation and diverges from the hardened pattern already adopted in the mainline `IntentsBase.sol` — which itself was patched for a documented reentrancy fee-theft class (see `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol`, which exists specifically because CEI ordering was previously exploited in the fill path). The Tron contract was evidently not brought in line with that fix. [3](#0-2) 

### Recommendation
Reorder `withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` to match `IntentsBase._withdraw()`: read the escrowed amount, revert if zero, write the decremented value to `_orders[body.commitment][token]` **before** performing either the native `.call` or the ERC-20 transfer. Additionally add a `nonReentrant` guard (or transient-storage reentrancy lock, as already used elsewhere in the codebase via `tstore`/`tload` in `placeOrder`) to every externally-triggered entry point that can reach `withdraw()`, and replace the raw ERC-20 `.call(...)` with `SafeERC20.safeTransfer` to avoid silently mishandling non-standard return values.

### Proof of Concept
Conceptual PoC (cannot be executed without full local build tooling in this environment):
1. Place a cross-chain order on the Tron-deployed `IntentGatewayV2` whose `inputs` escrow **both** native TRX and an ERC-20 token for the same commitment.
2. Fill the order on the destination chain naming a malicious contract as the beneficiary/solver, and have the destination gateway dispatch the `RedeemEscrow` `WithdrawalRequest` with `tokens = [native, erc20]` (native first).
3. When the source-chain `onAccept` → `withdraw()` executes the native-token `.call{value: amount}("")` to the malicious beneficiary, its fallback re-enters — if any exposed synchronous path exists in the deployed host/dispatch flow that lets it trigger a second pass through the escrow map for the same commitment before `_orders[commitment][erc20] -= amount` executes — the ERC-20 leg is paid twice.
4. Assert the gateway's ERC-20 balance drops by more than the amount recorded in `_orders[commitment][erc20]` prior to the fill, and that `_orders[commitment][erc20]` underflows/reverts only on the *third* attempt rather than the second, evidencing the double payout already occurred.

Confirming step 3's exact reentry trigger requires access to the Tron host/dispatcher's message-processing loop, which was not fully available in this index — a Devin session with full repository/build access should verify whether `onAccept`/`onGetResponse` can be invoked twice synchronously within a single relayer transaction before treating this PoC as fully proven.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L687-705)
```text
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
