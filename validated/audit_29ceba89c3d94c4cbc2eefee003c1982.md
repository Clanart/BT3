Found a concrete analog. The `VWAPOracle.recordSpread` mechanism (used as `IIntentPriceOracle` by `IntentGatewayV2.fillOrder`) is the same "false supply/demand signal via self-dealing" bug class as the NFT auction report — a self-fill can feed an arbitrarily favorable, volume-weighted price signal into an oracle that is trusted by other on-chain/off-chain participants (fillers and integrators use `VWAPOracle.spread()` as a pricing reference for same-token cross-chain swaps).

### Title
Same-token order self-fill can freely manipulate the VWAP price-spread oracle used by `IntentGatewayV2` - (File: `evm/src/utils/VWAPOracle.sol`)

### Summary
`IntentGatewayV2.fillOrder()` unconditionally calls `IIntentPriceOracle(_params.priceOracle).recordSpread(commitment, order.source, order.inputs, options.outputs)` after every fill [1](#0-0) . There is no restriction preventing the order creator (`order.user`) and the filling solver (`msg.sender`) from being the same economic actor (via a second, Sybil-controlled address), and the fill functions never validate that `outputs[i]` reflects a real market price — the solver can freely set `options.outputs[i].amount` to any value ≥ `order.output.assets[i].amount` [2](#0-1) . `VWAPOracle.recordSpread` then computes `spreadBps` directly from the input/output amounts and folds it into a persistent, volume-weighted cumulative average keyed by `(sourceChain, token)` [3](#0-2) .

### Finding Description
This is the direct on-chain analog of the reported NFT auction bug: a `require(msg.sender != owner)`-style self-dealing guard is trivially bypassed by using a second address, letting one economic actor manufacture a false signal (there, fake bid/demand; here, a fake favorable spread). Concretely:

1. A user places a same-token, cross-chain order (`order.inputs[i].token == order.output.assets[i].token`, `order.source != order.destination`) with a large `order.inputs[i].amount` and a deliberately generous `order.output.assets[i].amount`.
2. The user, from a second address they control (a Sybil solver), calls `fillOrder()` and supplies `options.outputs[i].amount` equal to or larger than required — the contract does not check the solver isn't the order's owner, nor does it validate the fill against any external reference price.
3. `_fillCrossChain`/`_fillSameChain` complete the swap (self-funded, so the "cost" is just moving funds between the attacker's own two addresses, net of protocol/surplus fees), then `fillOrder` calls `recordSpread` with the attacker-chosen input/output amounts [4](#0-3) .
4. `VWAPOracle` has no admin/owner restriction on which orders may contribute to the spread and no check that the filler is unrelated to the order creator — `recordSpread` is `restrict(_intentGateway)`-gated only at the caller level (must come from the configured `IntentGatewayV2`), not at the economic-actor level [5](#0-4) . The weighted spread accumulator can be pushed arbitrarily positive or negative, and large self-funded volume dominates the VWAP (demonstrated by the repo's own test showing a large fill's spread dominates VWAP over smaller organic fills) [6](#0-5) .

The existing self-dealing guards in this codebase (`Unauthorized()` checks tying `msg.sender` to `order.user` for cancel/authorization paths) do nothing to stop this, because `fillOrder` deliberately allows any `msg.sender` to act as solver — that is the intended permissionless-solver model — and there is no economic-identity check comparable to "solver cannot equal order owner" anywhere in `IntentGatewayV2`, `IntrinsicIntents`, or `ExtrinsicIntents` [7](#0-6) [8](#0-7) .

### Impact Explanation
`VWAPOracle.spread()` is documented as a "same-token swap" price reference and is wired into `IntentGatewayV2` as `_params.priceOracle`, meaning it is intended to be consulted by protocol logic and/or third-party integrators (fillers, risk systems) for pricing/overfill decisions. An attacker can cheaply and repeatedly self-fill same-token orders across chains to bias the cumulative VWAP in either direction, then exploit any downstream logic (in this or another `IntentGatewayV2` deployment, or third-party filler strategies) that trusts `spread()` as a legitimate market signal — e.g., to make manipulated fills appear "fair," to game overfill/clamp protections that key off recorded spread, or to mislead other solvers'/venues' pricing. This falls under "logic attacks" and "false state acceptance" against the Impact Gate: the oracle accepts a fabricated economic signal as if it were organic market activity, with no distinct-counterparty requirement.

### Likelihood Explanation
High. No privileged role, relayer, or prover is required — a single unprivileged attacker controlling two EOAs (order owner + solver) can place and self-fill a same-token, cross-chain order at will, on any deployment that wires a `priceOracle` into `IntentGatewayV2`. The only cost is transient escrow of their own funds plus protocol/surplus fees, which can be minimized by fills at par or slightly above/below par to still move the VWAP in the desired direction. This mirrors exactly the reported bug's "trivial Sybil bypass" — no on-chain mechanism distinguishes a self-fill from a genuine third-party fill.

### Recommendation
Do not treat `recordSpread` volume as unconditionally trustworthy market data. At minimum: (1) require or strongly encourage `_params.priceOracle` consumers to weight/cap the influence of any single `commitment` or `(order.user, filler)` pair on the VWAP, mirroring the recommendation in the original report ("do not rely on this mechanism being satisfied"); (2) consider tracking `order.user` on the fill path and either excluding same-address fills from `recordSpread`, or requiring `order.session`/solver-selection to be enabled with a distinct authorized solver before a fill counts toward the price oracle; (3) document explicitly (as the original report also recommends) that `VWAPOracle.spread()` is not Sybil-resistant and must not be used as the sole input to any fund-moving decision.

### Proof of Concept
1. Deploy `IntentGatewayV2` with `_params.priceOracle` set to a `VWAPOracle` instance, `solverSelection = false`.
2. Attacker address `A` places a cross-chain, same-token order: `order.inputs = [{token: DAI, amount: 1_000_000e18}]`, `order.output.assets = [{token: DAI, amount: 900_000e18}]`, `order.source = "EVM-1"`, `order.destination = "EVM-2"`.
3. From attacker address `B` (funded by `A` off-chain), call `fillOrder(order, {outputs: [{token: DAI, amount: 900_000e18}]})` on the destination chain — `_fillCrossChain` succeeds because there is no owner/solver distinctness check [9](#0-8) .
4. `fillOrder` calls `recordSpread(commitment, "EVM-1", inputs, outputs)`, recording a large-volume `-100_000/1_000_000 = -1000 bps` spread [1](#0-0) , dominating the VWAP as shown in `testVWAPSingleLargeFillVsManySmallFills`-style behavior [10](#0-9) .
5. Repeat with reversed amounts (output > input) to swing the VWAP positive instead — demonstrating the spread signal is entirely attacker-controlled at negligible net cost (self-funded, minus protocol/surplus fee bps).

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L442-451)
```text
        if (isSameChain) {
            _fillSameChain(order, options, commitment);
        } else {
            _fillCrossChain(order, options, commitment);
        }

        if (_params.priceOracle != address(0)) {
            IIntentPriceOracle(_params.priceOracle)
                .recordSpread(commitment, order.source, order.inputs, options.outputs);
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-106)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);

        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

            if (solverAmount < totalRequired) revert InvalidInput();
```

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

**File:** evm/tests/foundry/VWAPOracleTest.sol (L443-461)
```text
        TokenInfo[] memory outputs1 = new TokenInfo[](1);
        inputs1[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 1 * 1e18});
        outputs1[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 11 * 1e17}); // 1.1 tokens
        oracle.recordSpread(keccak256("order1"), sourceChain, inputs1, outputs1);

        // Fill 2: 1 million tokens, -10 bps
        TokenInfo[] memory inputs2 = new TokenInfo[](1);
        TokenInfo[] memory outputs2 = new TokenInfo[](1);
        inputs2[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 1_000_000 * 1e18});
        outputs2[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 999_000 * 1e18});
        oracle.recordSpread(keccak256("order2"), sourceChain, inputs2, outputs2);

        int256 vwap = oracle.spread(sourceChain, address(dai));

        // VWAP: (1000*1 + -10*1000000) / 1000001 = (1000 - 10000000) / 1000001 ≈ -9.99 bps
        // Tiny fill's huge spread (+1000 bps) is negligible compared to large volume at -10 bps
        assertTrue(vwap < 0, "VWAP should be dominated by large volume fill");
        assertEq(vwap, -9, "VWAP should be approximately -10 bps");
    }
```

**File:** evm/tests/foundry/VWAPOracleTest.sol (L463-490)
```text
    function testVWAPSingleLargeFillVsManySmallFills() public {
        _initOracle();

        // Compare: one 10000 token fill vs ten 1000 token fills, all same spread
        // Part 1: Single large fill
        TokenInfo[] memory inputs1 = new TokenInfo[](1);
        TokenInfo[] memory outputs1 = new TokenInfo[](1);
        inputs1[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 10000 * 1e18});
        outputs1[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 9950 * 1e18});
        oracle.recordSpread(keccak256("large"), sourceChain, inputs1, outputs1);

        int256 vwapSingle = oracle.spread(sourceChain, address(dai));

        // Part 2: Many small fills (use USDC to track separately)
        for (uint256 i = 0; i < 10; i++) {
            TokenInfo[] memory inputs = new TokenInfo[](1);
            TokenInfo[] memory outputs = new TokenInfo[](1);
            inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1000 * 1e6});
            outputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 995 * 1e6});
            oracle.recordSpread(keccak256(abi.encodePacked("small", i)), sourceChain, inputs, outputs);
        }

        int256 vwapMany = oracle.spread(sourceChain, address(usdc));

        // Both should have same VWAP since spread and total volume are identical
        assertEq(vwapSingle, vwapMany, "VWAP should be identical regardless of fill count");
        assertEq(vwapSingle, -50, "Both should have -50 bps VWAP");
    }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-60)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
```
