### Title
`VWAPOracle.spread()` returns a misleading zero for both "no data" and "genuine zero spread" - ([File: evm/src/utils/VWAPOracle.sol])

### Summary
`VWAPOracle.spread()` is the direct local analog of the `getGuardedValue` pattern: it returns `int256(0)` both when there is genuinely no recorded spread deviation and when there has been **no fill data at all** for a `(sourceChain, token)` pair (`data.totalVolume == 0`). There is no distinct sentinel, revert, or "insufficient data" signal, so a consumer cannot distinguish "verified zero spread" from "the oracle has never observed this pair."

### Finding Description
`spread()` is a `view` function meant to report the cumulative VWAP spread the oracle has observed for same-token cross-chain swaps: [1](#0-0) 

When `_tokenSpreads[chainHash][token]` has never been written to (i.e. `recordSpread` has never been called for that pair, or every call so far hit the `inputAmountNormalized == 0` / unconfigured-decimals skip path), `data.totalVolume` is `0` and the function returns `0` — identical to the value it would return if fills genuinely averaged to a 0 bps spread. This mirrors the `getGuardedValue` bug: an "insufficient data" condition is silently collapsed into the same numeric response as a legitimate reading, with no explicit revert or error code to signal absence of data, exactly as the external report calls out.

The `recordSpread` write path itself has additional silent-skip semantics that widen the population of "fake zero" states: it returns early on length mismatches/empty arrays, and it `continue`s per-token when `inputDecimals == 0` (unconfigured token decimals) — neither of which is visible to a `spread()` reader: [2](#0-1) 

The test suite documents this precisely as intended behavior rather than as a defect: an unconfigured token's spread reads back as `0`, same as a token with a real zero-spread history: [3](#0-2) 

### Impact Explanation
`VWAPOracle` implements `IIntentPriceOracle` and is wired to `IntentGatewayV2` (`_intentGateway` restricts `recordSpread` to the gateway, and the gateway imports `IIntentPriceOracle`): [4](#0-3) [5](#0-4) 

Any downstream logic (fillers, governance dashboards, risk parameters, or future on-chain consumers) that reads `spread()` to gauge whether a filler is systematically underpaying users on a given route cannot tell a genuinely healthy route (`0` bps historical spread) apart from a route the oracle has simply never priced. A newly-added or thinly-used `(sourceChain, token)` pair — including ones with unconfigured decimals — reports the same "all clear" `0` as a well-established, honest one. This can mask an absence of price-integrity monitoring as a positive signal, which is the exact misclassification risk the external report warns about for `getGuardedValue`.

Note: I was not able to fully trace whether any current on-chain contract in this repo consumes `spread()` for an automated (non-observational) decision — the search only surfaced the oracle, its interface, and its Foundry tests. The impact is best characterized as "false state acceptance" of an oracle read whose consumers should not currently exist as autonomous fund-moving logic in this snapshot, so the direct funds-loss magnitude is unconfirmed and should be validated against `IIntentGatewayV2`/filler integrations before treating this as high severity.

### Likelihood Explanation
The zero-collision is deterministic and always reachable: any freshly deployed oracle, any pair that hasn't yet had a fill, or any input token whose decimals were never registered via `init`/`onAccept` will read `spread() == 0`. No malicious actor, relayer, or governance action is required — it is a default/no-data state trivially reached by an unprivileged reader of a public `view` function.

### Recommendation
- Add an explicit "no data" indicator, e.g. return a struct `(bool hasData, int256 spreadBps)` or a `fillCount`-gated revert (`NoSpreadDataAvailable()`), instead of silently returning `0`.
- Alternatively expose `fillCount`/`totalVolume` alongside `spread()` so callers can distinguish "0 bps over N fills" from "0 fills."
- Apply the same fix to the `recordSpread` skip paths (unconfigured decimals, length mismatch) — consider emitting a dedicated event (e.g., `SpreadSkippedUnconfiguredDecimals`) rather than silently `continue`/`return`ing, so off-chain and on-chain monitoring can tell a skipped update from a genuine zero-spread fill.

### Proof of Concept
1. Deploy `VWAPOracle` and call `init` with no token-decimal configuration (as in `testInitialization`/`testRecordSpread_SkipsUnconfiguredSourceDecimals`).
2. Call `oracle.spread(sourceChain, address(dai))` before any fill is ever recorded → returns `0`.
3. Separately, record several fills whose weighted spread genuinely nets to `0` (e.g. `testVWAPWithZeroSpreads`'s intermediate states, or a single 1:1 fill as in `testRecordSpread_NoSpread`) → also returns `0`.
4. Both call sites in the Foundry suite assert `spread == 0` with no distinguishing return data: [6](#0-5) 
5. Any consumer treating `spread() == 0` as "route verified healthy" cannot distinguish these two states, reproducing the `getGuardedValue`-style ambiguity described in the external report.

### Citations

**File:** evm/src/utils/VWAPOracle.sol (L17-23)
```text
import {TokenInfo} from "@hyperbridge/core/apps/IntentGatewayV2.sol";
import {HyperApp} from "@hyperbridge/core/apps/HyperApp.sol";
import {IncomingPostRequest} from "@hyperbridge/core/interfaces/IApp.sol";
import {IDispatcher, PostRequest} from "@hyperbridge/core/interfaces/IDispatcher.sol";
import {IIntentPriceOracle} from "@hyperbridge/core/apps/IntentPriceOracle.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {Context} from "@openzeppelin/contracts/utils/Context.sol";
```

**File:** evm/src/utils/VWAPOracle.sol (L141-147)
```text
    function spread(bytes memory sourceChain, address token) external view returns (int256) {
        bytes32 chainHash = keccak256(sourceChain);
        CumulativeSpreadData memory data = _tokenSpreads[chainHash][token];
        if (data.totalVolume == 0) return 0;

        return data.weightedSpreadSum / int256(data.totalVolume);
    }
```

**File:** evm/src/utils/VWAPOracle.sol (L176-191)
```text
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

```

**File:** evm/tests/foundry/VWAPOracleTest.sol (L181-212)
```text
        oracle.init(host, updates);

        bytes32 commitment = keccak256("order1");
        TokenInfo[] memory inputs = new TokenInfo[](1);
        TokenInfo[] memory outputs = new TokenInfo[](1);
        // Input: 1000 USDC with 6 decimals on source
        // Output: 1000 USDC with 6 decimals on dest (read from contract)
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1000 * 1e6});
        outputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1000 * 1e6});

        oracle.recordSpread(commitment, sourceChain, inputs, outputs);

        int256 spread = oracle.spread(sourceChain, address(usdc));
        assertEq(spread, 0); // No spread
    }

    function testRecordSpread_SkipsUnconfiguredSourceDecimals() public {
        vm.prank(admin);
        VWAPOracle.TokenDecimalsUpdate[] memory updates = new VWAPOracle.TokenDecimalsUpdate[](0);
        oracle.init(host, updates);

        bytes32 commitment = keccak256("order1");
        TokenInfo[] memory inputs = new TokenInfo[](1);
        TokenInfo[] memory outputs = new TokenInfo[](1);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 1000 * 1e18});
        outputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 995 * 1e18});

        oracle.recordSpread(commitment, sourceChain, inputs, outputs);

        int256 spread = oracle.spread(sourceChain, address(dai));
        assertEq(spread, 0, "Should return 0 for tokens without configured decimals");
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L21-23)
```text
import {ICallDispatcher, Call} from "@hyperbridge/core/interfaces/ICallDispatcher.sol";
import {IDispatcher} from "@hyperbridge/core/interfaces/IDispatcher.sol";
import {IIntentPriceOracle} from "@hyperbridge/core/apps/IntentPriceOracle.sol";
```
