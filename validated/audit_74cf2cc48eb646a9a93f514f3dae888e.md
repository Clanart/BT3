## Finding Analysis [1](#0-0) 

`VWAPOracle.recordSpread` computes `outputDecimals` by calling `IERC20Metadata(outputToken).decimals()` directly on the `outputToken` address taken from the caller-supplied `outputs[i].token` array, with zero validation, bounds-checking, or try/catch protection against a malicious return value [2](#0-1) . This contrasts with the `inputToken` path, which relies on governance-configured storage (`_tokenDecimals[sourceChainHash][inputToken]`), showing the developers were aware decimals values need to be trusted/validated for the input side but left the output side to an arbitrary external call [3](#0-2) .

The only access control on this function is `restrict(_intentGateway)`, meaning any call routed through `IntentGatewayV2`'s fill flow can reach it [4](#0-3) . Since intent orders (including the destination/output token address) are defined by the order creator — an unprivileged, permissionless role — an attacker can create an order specifying a self-deployed malicious ERC20 as the output token, then fill it themselves (or via a colluding filler), causing `IntentGatewayV2` to invoke `recordSpread` with that malicious token as `outputToken`.

The malicious token's `decimals()` can return an extreme value (e.g. `0` or `36`). `_normalizeAmount` then scales the "normalized" output amount by many orders of magnitude in either direction [5](#0-4) , producing a `spreadBps` and `weightedSpread` wildly divorced from the real economic exchange rate, which is permanently accumulated into `_tokenSpreads[sourceChainHash][inputToken]` — a shared, non-resettable aggregate used for **all** future fills of that `(sourceChain, inputToken)` pair [6](#0-5) .

I was not able to fully trace, within the available tool budget, exactly how `spread()`/`decimals()` outputs are consumed downstream (e.g., in `SimplexPaymaster.sol`, which showed many related references) to confirm whether this corrupted VWAP feed directly triggers fund movement, refund miscalculation, or exclusivity/settlement decisions with monetary consequence. That linkage should be verified before finalizing severity, but the oracle input-validation gap itself is confirmed in code.

### Title
Unvalidated external `decimals()` call on attacker-controlled output token corrupts VWAPOracle's cumulative spread accounting - (File: evm/src/utils/VWAPOracle.sol)

### Summary
`VWAPOracle.recordSpread` normalizes fill amounts using `IERC20Metadata(outputToken).decimals()`, called on an attacker-controllable token address, without any sanity bound. A malicious ERC20 returning a spoofed `decimals()` value permanently skews the shared `CumulativeSpreadData` for the associated `(sourceChain, inputToken)` pair.

### Finding Description
`recordSpread` trusts an external, unauthenticated call to `outputToken.decimals()` to normalize amounts to 18 decimals before computing `spreadBps` and updating `weightedSpreadSum`/`totalVolume` [7](#0-6) . Because order creation and order fills are both unprivileged, an attacker can supply their own malicious ERC20 as the `outputToken`, returning an extreme `decimals()` (e.g. `0` or `36`), causing `_normalizeAmount` to massively inflate or deflate the computed output volume [5](#0-4) . The resulting corrupted `weightedSpread` is added to the shared, cumulative, non-resettable state for that `(sourceChainHash, inputToken)` key, which mixes with legitimate fills from other unrelated users of the same input token.

### Impact Explanation
This breaks the accurate price-normalization invariant of the oracle and can arbitrarily skew the VWAP spread reported by `spread()` for a given source-chain token, for as long as the corrupted state remains in the cumulative aggregate. If downstream logic (e.g. paymaster reward computation, fee/slashing decisions, or settlement-related pricing) consumes this spread value, the corruption could propagate into fund-related decisions. This part of the impact chain was not fully confirmed within available investigation.

### Likelihood Explanation
High — the attack requires no privileged role. Anyone can create an intent order specifying a self-controlled malicious token as `outputToken` and fill it themselves, immediately triggering `recordSpread` with attacker-controlled `decimals()`.

### Recommendation
Validate/bound the `decimals()` return value (e.g., reject or clamp to a reasonable range such as 0–24), use a `try/catch` with a safe fallback, and/or require destination tokens to be pre-registered (similar to the `_tokenDecimals` mapping used for source-chain tokens) rather than trusting an arbitrary external contract call.

### Proof of Concept
1. Deploy `MaliciousERC20` overriding `decimals()` to return `0` (or `36`).
2. As an unprivileged user, create an intent order whose `outputs[i].token` is `MaliciousERC20`.
3. Fill the order (self-fill or via a colluding filler) so that `IntentGatewayV2` invokes `VWAPOracle.recordSpread(...)` with `MaliciousERC20` as `outputToken`.
4. Observe `_normalizeAmount(outputs[i].amount, 0)` (or `36`) produces a wildly incorrect `outputAmountNormalized`, and assert `oracle.spread(sourceChain, inputToken)` diverges far from the real economic spread implied by actual token decimals.

### Citations

**File:** evm/src/utils/VWAPOracle.sol (L170-216)
```text
    function recordSpread(
        bytes32 commitment,
        bytes memory sourceChain,
        TokenInfo[] calldata inputs,
        TokenInfo[] calldata outputs
    ) external restrict(_intentGateway) {
        // Validate inputs and outputs have the same length
        if (inputs.length != outputs.length || inputs.length == 0) {
            return;
        }

        bytes32 sourceChainHash = keccak256(sourceChain);
        uint256 tokensLen = inputs.length;
        for (uint256 i = 0; i < tokensLen; i++) {
            address inputToken = address(uint160(uint256(inputs[i].token)));
            address outputToken = address(uint160(uint256(outputs[i].token)));

            // Get decimals for input token from storage (remote chain)
            // Native tokens (address(0)) use 18 decimals
            uint8 inputDecimals = inputToken == address(0) ? 18 : _tokenDecimals[sourceChainHash][inputToken];
            if (inputDecimals == 0) continue; // Skip if decimals not configured

            // Get decimals for output token directly from contract (local chain)
            // Native tokens (address(0)) use 18 decimals
            uint8 outputDecimals = outputToken == address(0) ? 18 : IERC20Metadata(outputToken).decimals();

            // Normalize both amounts to 18 decimals for comparison
            uint256 inputAmountNormalized = _normalizeAmount(inputs[i].amount, inputDecimals);
            uint256 outputAmountNormalized = _normalizeAmount(outputs[i].amount, outputDecimals);

            // Calculate spread for this token: (output - input) / input * 10000
            // Positive spread = filler provided more tokens (good for user)
            // Negative spread = filler provided fewer tokens (filler captured spread)
            int256 spreadBps = 0;
            if (inputAmountNormalized > 0) {
                int256 amountDiff = int256(outputAmountNormalized) - int256(inputAmountNormalized);
                spreadBps = (amountDiff * int256(BPS_DENOMINATOR)) / int256(inputAmountNormalized);
            }

            // Update cumulative spread data for this token (weighted by volume)
            int256 weightedSpread = spreadBps * int256(inputAmountNormalized);
            _updateCumulativeSpread(_tokenSpreads[sourceChainHash][inputToken], weightedSpread, inputAmountNormalized);

            // Emit event for each token
            emit SpreadRecorded(commitment, outputToken, spreadBps);
        }
    }
```

**File:** evm/src/utils/VWAPOracle.sol (L240-248)
```text
    function _normalizeAmount(uint256 amount, uint8 _decimals) private pure returns (uint256 normalized) {
        if (_decimals == 18) {
            return amount;
        } else if (_decimals < 18) {
            return amount * (10 ** (18 - _decimals));
        } else {
            return amount / (10 ** (_decimals - 18));
        }
    }
```
