## Analysis

The external report's core broken invariant: an economic quantity (`lockedInterests` distribution cap) is gated by an **instantaneously-read, unlocked balance** (`sanMint` / total SanToken supply) with no minimum holding period, letting a transient flashloan-inflated balance dominate a payout calculation within a single block.

The closest local analog is Hyperbridge's Phantom order **liquidity-weighted median pricing**, where a solver's live on-chain token balance is read via a bare `eth_call` at `"latest"` and used directly as the *weight* that determines the settlement price paid to real users.

### Title
Phantom order price snapshot uses an unlocked, instantaneously-read solver balance as bid weight, letting a flashloaned balance dominate the liquidity-weighted median that prices real intent settlements - (File: `sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts`)

### Summary
`aggregatePhantomBids` computes the settlement price for Phantom intents (`USDC → cNGN`, etc.) as a **liquidity-weighted median** of solver quotes. Each quote's weight is the solver's live balance of the output token, read with a plain `eth_call("balanceOf"/"maxWithdraw", "latest")` at `getTotalSolverBalance` [1](#0-0) . This mirrors exactly the sanRate bug's core flaw: a distribution-weighting value is taken from a spot balance with no minimum holding period, no block delay, and no TWAP — so it can be inflated for the duration of a single transaction/query and then reversed.

### Finding Description
The pricing pipeline works as follows:
1. A phantom order's bid window closes (`PhantomBidWindowExhausted`), and later, asynchronously, `aggregatePhantomBids` runs and fetches all bids via `intents_getBidsForOrder` [2](#0-1) .
2. For every verified bid, the solver's `weight` is computed by `getTotalSolverBalance`, which is a synchronous `eth_call` at the block tag `"latest"` at the moment the aggregation job happens to run — not the block at which the bid window closed, and not any historically-anchored height [3](#0-2) .
3. `weightedMedian` then picks the price at which cumulative weight first crosses half the total weight [4](#0-3) , and the tests confirm a single dominant-weight quote fully determines the median (`weight: 100n` pulls the median straight to its own `price`) [5](#0-4) .
4. This `medianPrice` is not just informational — the SDK explicitly uses it to size real order economics: `floor(netInput × medianPrice / standardAmount)` for exact-input quotes and the inverse for exact-output quotes [6](#0-5) , and it also feeds `queryAvailableLiquidity` [7](#0-6) .

There is no equivalent of `lastBlockUpdated`/vesting-lock here: nothing prevents a solver from temporarily inflating its `balanceOf`/`maxWithdraw` value (e.g., via a flashloan, a same-block deposit into a permissionless ERC-4626 vault it already integrates with, or a brief transfer-in from an affiliated account) for just long enough to cover the window during which the off-chain aggregator's `eth_call` executes, then reversing it immediately after. Unlike the `StreamingYieldVault.sol` primitive elsewhere in this repo — which explicitly defends against this exact class of attack by keying yield recognition to `block.timestamp` and disabling deposits mid-tranche [8](#0-7)  — the Phantom pricing path has no analogous lock, delay, or minimum-holding-period guard on the balance it reads.

### Impact Explanation
This falls under "transaction manipulation" / "false proof/state acceptance" impacts: an unprivileged, already-qualified solver (no relayer/prover/admin collusion required) can bias the liquidity-weighted median that prices real user intents. A solver can transiently balloon its `weight` to force `medianPrice` toward its own submitted `price`, causing:
- Users to receive materially less output than a fair-liquidity-weighted price would produce (their `amountOut`/`amountIn` is derived directly from `medianPrice`).
- `queryAvailableLiquidity` to misreport available liquidity, distorting downstream quoting decisions.

This is a direct "wrong beneficiary or amount" outcome on real settlement values, not merely an off-chain indexing quirk, since the price is consumed by the SDK's order-construction path.

### Likelihood Explanation
Medium-to-high. Any delegated solver can submit a bid; no relayer, prover, or governance compromise is required. The `eth_call` uses `"latest"`, so the attack window is simply "whenever the off-chain aggregator happens to sample," which a solver can trigger/predict by controlling when/how it responds to the bid window closing, or by holding an inflated balance across the plausible sampling window using cheap flashloan/vault-deposit primitives already supported by the same codebase (`maxWithdraw` on configured ERC-4626 vaults is explicitly swept as part of the weight) [9](#0-8) .

### Recommendation
- Anchor the balance read to a fixed, historically-committed block height (e.g., the block at which `PhantomBidWindowExhausted` fired, or N blocks prior) instead of `"latest"`, so a same-block/same-window balance inflation cannot influence the weight.
- Consider a minimum holding-period requirement (analogous to `lastBlockUpdated`) before a balance can count toward `weight`, or use a time-averaged balance across multiple blocks.
- Cap a single solver's contribution to the weighted median (a percentage-of-total-weight cap), mirroring the "maxSanRateUpdate"-style safeguard, so no single quote can dominate `weightedMedian` regardless of its instantaneous balance.

### Proof of Concept
1. Solver S is a legitimately EIP-7702-delegated solver for the target chain's `SolverAccount` (satisfies `isVerifiedSolverBid`).
2. S submits a bid on a Phantom order quoting an output amount favorable to itself (e.g., a low `solverAmount`, i.e., paying users less).
3. Immediately before/around when the aggregator's off-chain `getTotalSolverBalance` `eth_call` executes (predictable since it happens shortly after `PhantomBidWindowExhausted` and reads `"latest"`), S flashloans/transiently deposits a very large amount of the output token into a configured ERC-4626 vault (or its own address), so `balanceOf`/`maxWithdraw` momentarily reports a huge balance.
4. `getTotalSolverBalance` returns this inflated value as `weight` for S's quote [10](#0-9) .
5. `weightedMedian` picks S's `price` because its cumulative weight crosses 50% of total weight on its own [11](#0-10) .
6. S repays/withdraws the flashloaned/deposited funds.
7. The resulting `PhantomOrderPriceSnapshot.medianPrice` is now S's chosen price, and every subsequent SDK quote computed from it (`floor(netInput × medianPrice / standardAmount)`) shortchanges users relative to the true liquidity-weighted fair price.

### Citations

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L115-136)
```typescript
// Liquidity-weighted median of solver quotes. Each quote's influence is proportional to `weight` —
// the solver's total balance for the output token across native + vault venues — so a solver that
// can actually deliver size moves the price more than one quoting on thin liquidity. Returns the
// lower weighted median: the smallest price whose cumulative weight reaches half of the total.
// Zero-weight quotes contribute nothing; if every weight is zero it falls back to the unweighted
// median so a price is still reported.
export function weightedMedian(entries: { price: bigint; weight: bigint }[]): bigint {
	const sorted = [...entries].sort((a, b) => (a.price < b.price ? -1 : a.price > b.price ? 1 : 0))
	const totalWeight = sorted.reduce((acc, e) => (e.weight > 0n ? acc + e.weight : acc), 0n)

	if (totalWeight === 0n) {
		return sorted[Math.floor(sorted.length / 2)].price
	}

	let cumulative = 0n
	for (const entry of sorted) {
		if (entry.weight <= 0n) continue
		cumulative += entry.weight
		if (cumulative * 2n >= totalWeight) return entry.price
	}
	return sorted[sorted.length - 1].price
}
```

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L329-337)
```typescript
export async function fetchBidsForOrder(nodeUrl: string, commitment: string): Promise<RpcBidInfo[]> {
	const data = await rpcCall(nodeUrl, {
		id: 1,
		jsonrpc: "2.0",
		method: "intents_getBidsForOrder",
		params: [commitment],
	})
	return Array.isArray(data.result) ? (data.result as RpcBidInfo[]) : []
}
```

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L339-370)
```typescript
async function ethCallUint(evmRpcUrl: string, to: string, data: string): Promise<bigint> {
	try {
		const result = await rpcCall(evmRpcUrl, {
			id: 1,
			jsonrpc: "2.0",
			method: "eth_call",
			params: [{ to, data }, "latest"],
		})
		if (result.error || !result.result || result.result === "0x") return 0n
		return BigInt(result.result)
	} catch {
		return 0n
	}
}

// Sums the solver's redeemable balance of a single token on its destination chain: the raw ERC-20
// balance plus any ERC-4626 vault positions wrapping it.
async function getTotalSolverBalance(
	evmRpcUrl: string,
	chain: string,
	token: string,
	solver: string,
	yieldVaults: YieldVaultMap,
): Promise<bigint> {
	const padded = solver.replace("0x", "").padStart(64, "0")
	const raw = await ethCallUint(evmRpcUrl, token, `0x70a08231${padded}`) // balanceOf(address)
	const vaults = yieldVaults[chain]?.[token.toLowerCase()] ?? []
	const vaultBalances = await Promise.all(
		vaults.map((v) => ethCallUint(evmRpcUrl, v, `0xce96cb77${padded}`)), // maxWithdraw(address)
	)
	return vaultBalances.reduce((acc, b) => acc + b, raw)
}
```

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L505-508)
```typescript
			// Price influence: the solver's liquidity in the output token on the destination chain.
			const outputTokenAddress = toAddress(fillData.outputToken)
			const weight = await getTotalSolverBalance(destUrl, chain, outputTokenAddress, solver, yieldVaults)
			quotes.push({ price: fillData.solverAmount, weight })
```

**File:** sdk/packages/sdk/src/tests/phantomAggregation.test.ts (L94-102)
```typescript
	it("weights quotes by balance — the high-liquidity solver pulls the median to its price", () => {
		const quotes = [
			{ price: 100n, weight: 1n },
			{ price: 200n, weight: 1n },
			{ price: 300n, weight: 100n },
		]
		// Total weight 102; cumulative reaches half (>=51) only at price 300.
		expect(weightedMedian(quotes)).toBe(300n)
	})
```

**File:** docs/content/developers/sdk/api/intent-gateway.mdx (L230-232)
```text
`amountIn` and `amountOut` already account for the IntentGateway protocol fee that the gateway deducts from order inputs. Exact-input quotes price the swap against the post-fee input, so `amountOut` is the snapshot-priced output; exact-output quotes return the gross `amountIn` required to produce the requested `amountOut`. Use the returned amounts directly as the order's `inputs` and `output.assets`—no further fee or slippage adjustment is required.

For an exact-input Phantom quote, the SDK deducts the gateway fee and computes `floor(netInput × medianPrice / standardAmount)`. For an exact-output quote, it computes `ceil(amountOut × standardAmount / medianPrice)` and grosses that input up for the gateway fee. A missing indexer, missing snapshot, or invalid snapshot throws an explicit error; the SDK does not silently switch price sources.
```

**File:** docs/content/developers/sdk/api/intent-gateway.mdx (L234-258)
```text
### queryAvailableLiquidity(params)

Returns the total output-token liquidity measured in the latest directional
Phantom snapshot. For `USDC → cNGN`, the result is the amount of cNGN held by
eligible solvers for that snapshot at its measurement time.

```typescript lineNumbers
import { createQueryClient, IntentGateway } from "@hyperbridge/sdk"

const gateway = (await IntentGateway.create(source, dest)).withQueryClient(
  createQueryClient({ url: "https://nexus.indexer.polytope.technology" }),
)

const liquidity = await gateway.queryAvailableLiquidity({
  tokenIn: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
  tokenOut: "0x46C85152bFe9f96829aA94755D9f915F9B10EF5F",
})

if (liquidity) {
  console.log(liquidity.totalLiquidity, "cNGN")
  console.log("Providers:", liquidity.providerCount)
  console.log("Liquidity by chain:", liquidity.liquidityByChain)
  console.log("Measured at:", liquidity.snapshotTime)
}
```
```

**File:** sdk/packages/core/contracts/vaults/StreamingYieldVault.sol (L163-170)
```text
    /// @dev Linear unlock of the current tranche, keyed on `block.timestamp` so that a deposit
    ///      and withdrawal within the same block observe an identical, unchanged share price.
    function _lockedYield() internal view returns (uint256) {
        uint256 start = _vestingStart;
        uint256 elapsed = block.timestamp - start;
        if (elapsed >= VEST) return 0;
        return (_vestingAmount * (VEST - elapsed)) / VEST;
    }
```
