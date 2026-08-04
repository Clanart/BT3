### Title
VWAP oracle spread can be driven to an arbitrary value via near-zero-cost self-dealt intent fills - (File: `evm/src/utils/VWAPOracle.sol`)

### Summary
`VWAPOracle.recordSpread` is meant to track a **volume-weighted** average price spread for same-token cross-chain swaps, mirroring the exact fix the Tracer report recommended (weight by fill size instead of counting trades equally). However, the weighting formula algebraically cancels the input volume out of the numerator, so the "weight" recorded in `totalVolume` no longer bounds the influence of a fill on the running average. Combined with the Intent Gateway's fill mechanics — where the filler of an order is repaid the full escrowed input — an attacker can generate fills whose input amount (the denominator/weight) is near zero while the output amount (which drives the numerator) is chosen arbitrarily, at close to zero net cost, exactly as in the original Tracer finding where near-zero-amount, self-paired trades skewed the average price.

### Finding Description
`recordSpread` computes, per fill: [1](#0-0) 

```
spreadBps    = (output - input) * 10000 / input
weightedSpread = spreadBps * input        // == (output - input) * 10000
_updateCumulativeSpread(..., weightedSpread, input)   // totalVolume += input
```

Note that `weightedSpread` simplifies to `(output - input) * 10000` — the `input` term cancels out of the numerator entirely. Meanwhile `totalVolume` (the true weight/denominator used by `spread()`) only accumulates the raw `input` amount: [2](#0-1) 

So `spread() = Σ(output_i - input_i)*10000 / Σ input_i`. If an attacker can create a fill with an arbitrarily small `input` and an arbitrarily large `output`, the numerator scales with the absolute (output − input) while the denominator stays pinned near zero — the "volume weighting" that was supposed to prevent price manipulation (per the fix recommended in the referenced Tracer report) provides no real protection here, because the weight and the manipulated quantity are not actually coupled.

`recordSpread` is only callable by the configured `_intentGateway` contract: [3](#0-2) 

but that gateway calls it for every ordinary user fill (`IntentGatewayV2` / `IntrinsicIntents`). Fill mechanics documented and tested show the filler (solver) is repaid the escrowed input in full once a fill completes: [4](#0-3) [5](#0-4) 

and for same-chain orders the solver directly receives the proportional escrowed input as soon as they supply the output: [6](#0-5) 

Nothing in the reviewed fill path requires the order creator (`order.user`) and the filler (`msg.sender`) to be different addresses, nor does it require the output `beneficiary` to be a third party. An attacker acting as both the order creator and the filler:
1. Places an order with a tiny `input` amount (e.g. 1 wei of the source token) and an output amount they choose arbitrarily large, with `beneficiary` set to an address they control.
2. Fills their own order, paying the (self-chosen, self-owned) output amount to their own beneficiary address, and immediately receives back the tiny escrowed input as the filler's repayment.
3. `recordSpread` is invoked with `input ≈ 0` and `output` arbitrarily large, driving `weightedSpread = (output-input)*10000` to an attacker-chosen huge value while `totalVolume` barely increases.
4. Repeating this (each iteration costs only gas plus a self-transfer of the output token, which the attacker also controls/receives) lets the attacker push `spread()` to essentially any value they want, in either direction.

### Impact Explanation
This is the direct on-chain analog of the H-01 Tracer finding: a market-tracking average that is nominally weighted by trade size can be manipulated by an unprivileged actor who trades with themselves using a lopsided, near-zero-cost input/output pair, because the weighting formula does not actually tie the recorded weight to genuine economic exposure. `VWAPOracle` implements `IIntentPriceOracle`, Hyperbridge's designated price-integrity source for same-token intent fills; any downstream logic that trusts `spread()` (e.g. to detect/bound abnormal solver spread capture, or to price/limit intents) would consume a falsified, attacker-controlled value. I was not able to confirm within this pass which specific downstream contract or off-chain consumer reads `IIntentPriceOracle.spread()` for a fund-affecting decision, so the precise blast radius (e.g., whether it gates a payout, a fee, or a risk parameter) needs to be confirmed against `IIntentPriceOracle` consumers before treating this as a direct fund-loss primitive rather than a false-state-acceptance/oracle-integrity issue.

### Likelihood Explanation
The primitive requires only an ordinary, permissionless intent order + self-fill — no privileged relayer, prover, or governance role, and no more capital than the attacker is willing to move to their own beneficiary address (the escrowed input itself is fully refunded to the attacker as filler). This mirrors exactly the "near-zero amount, self-paired trade, repeated in bulk" primitive that Tracer confirmed as valid and worth fixing.

### Recommendation
Change the recorded weight so it cannot be decoupled from the manipulated quantity, e.g.:
- Weight by `min(input, output)` (or some non-manipulable measure of the actual value exchanged) rather than allowing the numerator to be driven purely by `output` while the denominator is pinned to a near-zero `input`.
- Enforce a minimum absolute input/output size (denominated in a stable, normalized unit) before a fill contributes to `_tokenSpreads`, so dust-sized fills cannot dominate `weightedSpreadSum`.
- Consider disallowing/discounting fills where `order.user == filler` or `beneficiary` is controlled by the filler, since self-dealt fills carry no genuine price-discovery signal.

### Proof of Concept
Conceptual sequence (mirrors the original report's "near-zero amount, self-paired trade" primitive):
1. Attacker deploys/uses an EOA `A`.
2. `A` places an intent order with `inputs[0].amount = 1` (smallest unit) and `output.assets[0].amount = X` (arbitrarily large), `output.beneficiary = A`.
3. `A` calls `fillOrder`/`_fillSameChain` as the solver, supplying `X` output tokens to itself and receiving the `1`-unit input escrow back.
4. This triggers `IntentGatewayV2` → `VWAPOracle.recordSpread(commitment, sourceChain, inputs, outputs)` with `inputAmountNormalized ≈ 1`, `outputAmountNormalized = X`.
5. `spreadBps = (X-1)*10000/1` (huge), `weightedSpread = (X-1)*10000`, `totalVolume += 1`.
6. Repeating steps 2–5 lets `A` set `oracle.spread(sourceChain, token)` to essentially any value, since `Σ weightedSpread / Σ totalVolume` is dominated entirely by attacker-chosen `X` values against a denominator `A` also fully controls.

I could not execute this against the Foundry test suite in this pass (`evm/tests/foundry/VWAPOracleTest.sol` only exercises honest, non-self-dealt fills), so this should be validated with a dedicated Foundry test that self-fills via `IntentGatewayV2`/`IntrinsicIntents` and asserts `oracle.spread(...)` moves far beyond what a genuine trade of that same nominal size would produce.

### Citations

**File:** evm/src/utils/VWAPOracle.sol (L141-147)
```text
    function spread(bytes memory sourceChain, address token) external view returns (int256) {
        bytes32 chainHash = keccak256(sourceChain);
        CumulativeSpreadData memory data = _tokenSpreads[chainHash][token];
        if (data.totalVolume == 0) return 0;

        return data.weightedSpreadSum / int256(data.totalVolume);
    }
```

**File:** evm/src/utils/VWAPOracle.sol (L170-180)
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

```

**File:** evm/src/utils/VWAPOracle.sol (L203-211)
```text
            int256 spreadBps = 0;
            if (inputAmountNormalized > 0) {
                int256 amountDiff = int256(outputAmountNormalized) - int256(inputAmountNormalized);
                spreadBps = (amountDiff * int256(BPS_DENOMINATOR)) / int256(inputAmountNormalized);
            }

            // Update cumulative spread data for this token (weighted by volume)
            int256 weightedSpread = spreadBps * int256(inputAmountNormalized);
            _updateCumulativeSpread(_tokenSpreads[sourceChainHash][inputToken], weightedSpread, inputAmountNormalized);
```

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L41-47)
```text
### Fill Flow

The solver calls `fillOrder(order, options)` on the **destination chain**. The function verifies the order hasn't expired (`order.deadline >= block.number`), confirms execution is on the correct chain, and checks the order hasn't already been filled. The solver must provide output amounts greater than or equal to the order's required amounts — any amount below the required amount reverts with `InvalidInput()`.

If the solver provides more tokens than required, the excess (surplus) is split according to `surplusShareBps`. If the order includes calldata, 100% of surplus goes to the protocol to prevent manipulation.

After delivering output tokens to the beneficiary, the contract dispatches a cross-chain `RedeemEscrow` message back to the source chain.
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L2128-2134)
```text
        uint256 fillerBalanceBefore = usdc.balanceOf(filler);

        vm.prank(address(host));
        intentGateway.onAccept(IncomingPostRequest({relayer: address(0), request: request}));

        assertEq(usdc.balanceOf(filler) - fillerBalanceBefore, inputAmount, "Filler should receive escrowed tokens");
    }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L91-123)
```text
                    beneficiaryShare = dust - protocolShare;
                }
            } else {
                fillAmount = solverAmount > remaining ? remaining : solverAmount;
            }

            uint256 amountFilled = alreadyFilled + fillAmount;
            _partialFills[commitment][outputToken] = amountFilled;
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

            if (totalRequired > amountFilled) isFullyFilled = false;
            if (protocolShare > 0) emit DustCollected(token, protocolShare);

            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
            outputFills[i] = TokenInfo({token: outputToken, amount: fillAmount});
```
