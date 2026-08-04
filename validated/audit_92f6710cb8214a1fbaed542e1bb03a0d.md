## Analysis

The external report describes a classic "fee rounds to zero via amount-splitting" bug: `ownerShare = (_totalAmount * ownerFeePerDepositPercent) / 10_000` truncates to `0` when `_totalAmount * feePercent < 10_000`, letting an attacker split a large purchase into many sub-threshold chunks and never pay the fee.

The same arithmetic pattern exists in Hyperbridge's `IntentGatewayV2.placeOrder()`, which computes the protocol fee taken from each escrowed input: [1](#0-0) 

```solidity
if (protocolFeeBps > 0) {
    reducedInputs = new TokenInfo[](inputsLen);
    for (uint256 i; i < inputsLen;) {
        uint256 originalAmount = order.inputs[i].amount;
        if (originalAmount == 0) revert InvalidInput();
        uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
        uint256 reducedAmount = originalAmount - protocolFee;
        ...
        if (protocolFee > 0) emit DustCollected(token, protocolFee);
        reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
```

The only guard against a degenerate input is `originalAmount == 0`; there is no minimum-order-size check ensuring `originalAmount * protocolFeeBps >= 10_000`. The same unguarded fee math is duplicated verbatim across the Tron port and the SDK's bundled contract copy: [2](#0-1)  and [3](#0-2) .

Documentation itself acknowledges the formula and current live rate (5 bps): [4](#0-3) , and the test suite exercises the exact `(amount * bps) / 10_000` computation without ever testing the zero-rounding boundary: [5](#0-4) .

### Title
Protocol fee rounds down to zero on small orders, letting fee-free order placement via amount-splitting - (File: evm/src/apps/IntentGatewayV2.sol)

### Summary
`IntentGatewayV2.placeOrder()` computes the protocol fee as `(originalAmount * protocolFeeBps) / 10_000` with no floor/minimum-amount check. Any unprivileged user can place orders whose `originalAmount` is small enough that `originalAmount * protocolFeeBps < 10_000`, making `protocolFee` truncate to `0`. By repeatedly splitting a large intent into many such sub-threshold orders, a user pays zero protocol fee on the entire notional value, permanently depriving the protocol treasury of fee revenue it is entitled to on every order.

### Finding Description
`placeOrder()` reads the effective fee rate (`_destinationProtocolFees[destinationHash]` or the global `_params.protocolFeeBps`) and applies it per input: [6](#0-5) 

For any `originalAmount` such that `originalAmount * protocolFeeBps < 10_000`, integer division yields `protocolFee = 0`, so `reducedAmount = originalAmount` and no `DustCollected` event fires. The only validation present is `originalAmount == 0` — there is no `AmountInvalid`-style revert enforcing a minimum viable order size relative to the fee rate, unlike the recommendation given in the original report (`revert` when `_totalAmount * feePercent < FLOAT_HANDLER_TEN_4`).

Because `protocolFeeBps` can be as low as 5 bps in production (per the docs) and stablecoins used as inputs can have low decimals (e.g., 6-decimal USDC, or lower-decimal tokens), the sub-threshold amount is a small but nonzero, economically usable unit — e.g., with `protocolFeeBps = 5`, any `originalAmount < 2000` (in the token's base units) pays zero fee. An attacker can place arbitrarily many such orders (each escrowing real value and eligible for solver fill) to move the full notional amount through the gateway while the treasury collects nothing, defeating the fee mechanism entirely. This is a pure logic/arithmetic gap reachable by any caller of the public `placeOrder()` entrypoint — no relayer, prover, or governance compromise is required.

### Impact Explanation
This does not steal user or solver funds directly, but it causes systemic loss of protocol fee revenue — the exact "logic attack" / fund-loss class the bounty gate calls out, mirroring the original report's owner-fee-griefing scenario. Given the mechanism is deterministic and requires no privileged actor, any sufficiently motivated user (or bot) can route arbitrarily large volumes through the gateway fee-free, undermining the DustCollected/`SweepDust` revenue model relied on for protocol economics.

### Likelihood Explanation
Likelihood is high: the exploit requires only calling the already-public, unprivileged `placeOrder()` function with a carefully chosen small `originalAmount`, repeated as many times as needed. No race conditions, governance actions, or off-chain cooperation are needed. The main friction is gas cost per split order, which is mitigated on low-fee L2 destinations where Hyperbridge's IntentGateway is commonly deployed.

### Recommendation
Add a minimum-order check mirroring the original recommendation: revert when `protocolFeeBps > 0 && originalAmount * protocolFeeBps < 10_000` (i.e., when the fee would truncate to zero despite a nonzero configured rate), for every input in the loop in `placeOrder()` (and its Tron/SDK-embedded duplicates). This ensures no order can bypass the protocol fee purely through amount-splitting.

### Proof of Concept
1. Governance sets `protocolFeeBps = 5` (matches documented production default).
2. Attacker calls `placeOrder()` with `order.inputs[0].amount = 1999` (in the input token's base units).
3. `protocolFee = (1999 * 5) / 10_000 = 0` — order is escrowed at full `1999` with zero fee taken and no `DustCollected` event.
4. Attacker repeats step 2 with many separate `placeOrder()` calls (each ≤ 1999 units) to move an arbitrarily large aggregate notional through the gateway, paying zero protocol fee overall, versus the intended `~0.05%` fee on a single large order.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L300-331)
```text
        // Phase 2: Compute protocol fees and commitment from actual received amounts.
        bytes32 destinationHash = keccak256(order.destination);
        uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
        if (protocolFeeBps == 0) {
            protocolFeeBps = _params.protocolFeeBps;
        }
        TokenInfo[] memory reducedInputs;
        bytes32 commitment;

        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                if (originalAmount == 0) revert InvalidInput();
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }

            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
        } else {
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L353-368)
```text
        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                // Emit DustCollected for protocol fee if non-zero
                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }
```

**File:** sdk/packages/core/contracts/apps/IntentGatewayV2.sol (L99-107)
```text
    /// @dev The percentage of surplus (in basis points) that goes to the protocol. The rest goes to beneficiary.
    /// 10000 = 100%, 5000 = 50%, etc.
    uint256 surplusShareBps;
    /// @dev The protocol fee in basis points charged on order inputs.
    /// 10000 = 100%, 100 = 1%, etc.
    uint256 protocolFeeBps;
    /// @dev The address of the price oracle contract.
    address priceOracle;
}
```

**File:** docs/content/developers/evm/intent-gateway/placing-orders.mdx (L94-111)
```text
### Protocol fees

Before escrowing, the contract deducts a protocol fee from each input amount:

```
protocolFee = input.amount × protocolFeeBps / 10_000
reducedAmount = input.amount − protocolFee
```

The fee is retained in the gateway as dust (emitting `DustCollected`), and per-destination overrides take precedence over the global `protocolFeeBps` when set. Current deployments charge **5 bps (0.05%)**. For a 100 USDC input:

| Item | Amount |
| --- | ---: |
| USDC transferred from your wallet | 100.000000 USDC |
| Protocol fee: `100 × 5 / 10,000` | 0.050000 USDC |
| Amount actually escrowed and offered to solvers | 99.950000 USDC |

The commitment hash is computed over the **fee-reduced inputs** — solvers read the reduced amounts from the `OrderPlaced` event and only need to match those. The fee is deducted at placement and is **not refunded** if the order expires, receives no bids, or is cancelled; a cancellation returns the remaining escrow and `order.fees`, but not the protocol fee.
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L3027-3044)
```text
    function testProtocolFeeWith1Percent() public {
        // Test with 1% protocol fee (100 basis points)
        IntentGatewayV2 customGateway = _deployGatewayProxy();
        Params memory customParams = Params({
            host: address(host),
            dispatcher: address(dispatcher),
            solverSelection: false,
            surplusShareBps: 10000,
            protocolFeeBps: 100, // 1%
            priceOracle: address(0)
        });
        bytes[] memory peers = new bytes[](1);
        peers[0] = host.host();
        customGateway.initialize(customParams, peers);

        uint256 inputAmount = 1000 * 1e6; // 1000 USDC
        uint256 expectedProtocolFee = (inputAmount * 100) / 10000; // 10 USDC
        uint256 expectedAmountAfterFee = inputAmount - expectedProtocolFee; // 990 USDC
```
