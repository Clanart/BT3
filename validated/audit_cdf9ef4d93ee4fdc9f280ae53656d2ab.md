[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L309-331)
```text
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

**File:** modules/pallets/intents-coprocessor/src/types.rs (L44-53)
```rust
	pub solver_selection: bool,
	/// The percentage of surplus (in basis points) that goes to the protocol
	/// 10000 = 100%, 5000 = 50%, etc.
	pub surplus_share_bps: U256,
	/// The protocol fee in basis points charged on order inputs
	/// 10000 = 100%, 100 = 1%, etc.
	pub protocol_fee_bps: U256,
	/// The address of the price oracle contract
	pub price_oracle: H160,
}
```

**File:** sdk/packages/core/contracts/apps/IntentGatewayV2.sol (L99-118)
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

/**
 * @dev Struct to define the destination fee parameters.
 */
struct DestinationFee {
    /// @dev The percentage of fee (in basis points) charged for the destination chain.
    /// 10000 = 100%, 5000 = 50%, etc.
    uint256 destinationFeeBps;
    /// @dev The state machine ID associated with the destination fee.
    bytes chain;
}
```

**File:** sdk/packages/sdk/src/protocols/intents/quote/shared.ts (L8-57)
```typescript
const BPS_DENOMINATOR = 10_000n

export function validateQuoteParams(params: QuoteIntentParams): void {
	const hasAmountIn = params.amountIn !== undefined
	const hasAmountOut = params.amountOut !== undefined
	if (hasAmountIn === hasAmountOut) throw new Error("Provide exactly one of amountIn or amountOut")
	if (params.amountIn !== undefined && params.amountIn <= 0n) throw new Error("amountIn must be greater than zero")
	if (params.amountOut !== undefined && params.amountOut <= 0n) throw new Error("amountOut must be greater than zero")
	if (params.tokenIn.toLowerCase() === params.tokenOut.toLowerCase()) {
		throw new Error("tokenIn and tokenOut cannot be the same")
	}
}

export async function readProtocolFeeBps(
	chainConfigService: ChainConfigService,
	source: IntentQuoteChainContext,
): Promise<bigint> {
	const gatewayAddress = chainConfigService.getIntentGatewayAddress(source.stateMachineId)
	if (!gatewayAddress || gatewayAddress === "0x" || gatewayAddress === zeroAddress) {
		throw new Error(`IntentGatewayV2 is not configured for chain ${source.stateMachineId}`)
	}

	const gatewayParams = (await source.client.readContract({
		address: gatewayAddress,
		abi: IntentGatewayV2.ABI,
		functionName: "params",
	})) as GatewayParams

	const protocolFeeBps = Array.isArray(gatewayParams)
		? BigInt(gatewayParams[4] as bigint | number | string)
		: BigInt((gatewayParams as GatewayParamsObject).protocolFeeBps ?? 0)
	if (protocolFeeBps < 0n || protocolFeeBps >= BPS_DENOMINATOR) {
		throw new Error(`Invalid IntentGateway protocol fee: ${protocolFeeBps} bps`)
	}
	return protocolFeeBps
}

/** Mirrors the gateway's floored fee deduction. */
export function deductProtocolFee(amount: bigint, protocolFeeBps: bigint): bigint {
	if (protocolFeeBps <= 0n) return amount
	const fee = (amount * protocolFeeBps) / BPS_DENOMINATOR
	return amount - fee
}

/** Conservatively grosses a net amount up so protocol-fee deduction cannot leave it short. */
export function grossUpForProtocolFee(netAmount: bigint, protocolFeeBps: bigint): bigint {
	if (protocolFeeBps <= 0n) return netAmount
	if (protocolFeeBps >= BPS_DENOMINATOR) throw new Error("protocolFeeBps must be less than 10,000")
	return divCeil(netAmount * BPS_DENOMINATOR, BPS_DENOMINATOR - protocolFeeBps)
}
```
