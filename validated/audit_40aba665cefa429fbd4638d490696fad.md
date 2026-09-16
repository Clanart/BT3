### Title
Unbounded precompiled-contract execution bypasses the per-transaction opcode-computation-cost DoS limit - ([File: blockchain/vm/evm.go])

### Summary
Kaia enforces a per-transaction wall-clock computation budget (`OpcodeComputationCostLimit` / `OpcodeComputationCostLimitCancun`, 100–150 ms) on top of ordinary EVM gas accounting, specifically to bound how long a single transaction can keep a node's EVM busy, independent of gas pricing accuracy. For normal bytecode opcodes this budget is enforced *before* the opcode executes. For precompiled contracts it is only accounted *after* the (potentially very expensive) `Run()` call has already completed, so a single call into a computation-heavy precompile can consume CPU time far beyond the intended per-tx budget before the limiter ever gets a chance to stop it.

### Finding Description
The interpreter's main loop enforces the computation-cost budget defensively: it adds the opcode's `computationCost` to `evm.opcodeComputationCostSum` and checks the running total against `evm.Config.ComputationCostLimit` **before** calling `operation.execute(...)`, aborting with `ErrOpcodeComputationCostLimitReached` if the budget is already exhausted: [1](#0-0) 

However, the dispatcher that runs precompiled contracts, `run()`, does the opposite: it unconditionally executes `RunPrecompiledContract(p, input, contract, evm)` to completion, and only *afterward* adds the returned `computationCost` to `evm.opcodeComputationCostSum` — there is no check against `ComputationCostLimit` at all in this path, before or after: [2](#0-1) 

`RunPrecompiledContract` itself only gates execution on `contract.UseGas(gas)` (ordinary EVM gas), not on the computation-cost budget: [3](#0-2) 

Several precompiles have `computationCost` that scales with attacker-controlled input while remaining affordable in gas terms (e.g. BLS12-381 `G1MultiExp`/`G2MultiExp`/pairing, legacy `bigModExp` under Byzantium pricing rules, `bn256` pairing). For example the BLS12-381 G2 multi-exponentiation precompile scales computation cost linearly with the number of point/scalar pairs `k` in the input: [4](#0-3) 

Because none of this cost is checked before or during the `Run()` call, a caller can submit a single CALL to such a precompile with an input sized to consume, in one shot, computation cost/CPU time that is a large multiple of `OpcodeComputationCostLimit` (100,000,000 ns) or `OpcodeComputationCostLimitCancun` (150,000,000 ns) — as long as they can pay the (comparatively far cheaper) gas cost. The budget check only takes effect afterward, when the *next* opcode is dispatched, at which point the excess CPU time has already been spent by every node that executes/validates the transaction. This defeats the purpose of the computation-cost limiter, which exists precisely to bound per-tx CPU usage regardless of gas-price/CPU-time mismatches for specific precompiles (the same class of pricing/CPU mismatch that CVE-2019-2507's "optimizer causes hang" bug class targets: an operation whose real-world resource cost is disproportionate to what the pricing/throttling mechanism assumes).

### Impact Explanation
Any unprivileged transaction sender or contract deployer who can get a `CALL`/`STATICCALL`/`DELEGATECALL` to a computation-heavy precompile executed (directly or via a deployed contract) can force every node validating that transaction (block proposers and syncing/verifying nodes) to spend CPU time on that single call far beyond the intended `OpcodeComputationCostLimit`/`OpcodeComputationCostLimitCancun` ceiling, since the limiter never gets to intervene mid-precompile. Because this happens identically on every node re-executing the block, it acts as a per-transaction CPU amplification/DoS vector that bypasses a security control that was specifically designed to cap it, and can be repeated across multiple transactions/calls within a block to further extend the effect. This matches "Medium" severity DoS impact analogous to CVE-2019-2507 (repeatable hang/availability degradation caused by unprivileged, network-reachable input hitting an under-throttled expensive-computation code path).

### Likelihood Explanation
The precompile addresses (ECRECOVER, SHA256, RIPEMD160, `bigModExp`, `bn256Add/ScalarMul/Pairing`, BLS12-381 G1/G2 Add/MultiExp/Pairing/Map) are all reachable via ordinary `CALL`/`STATICCALL` from any contract, requiring no special privilege, governance parameter, or node cooperation — only a funded account able to submit a transaction with enough gas to pay for one expensive precompile call. Constructing an input that maximizes `computationCost` per gas unit (e.g., maximal `k` for BLS12-381 multi-exp, or large exponent/modulus lengths for legacy `bigModExp`) is a straightforward, deterministic input-crafting exercise, making exploitation highly likely once gas affordability is confirmed.

### Recommendation
Enforce the `ComputationCostLimit` check for precompiled contracts symmetrically with the interpreter path: check `evm.opcodeComputationCostSum + expectedComputationCost` against `evm.Config.ComputationCostLimit` in `run()` (in `blockchain/vm/evm.go`) *before* invoking `RunPrecompiledContract`, returning `ErrOpcodeComputationCostLimitReached` (or refusing the call) if the pre-execution budget check would already be exceeded — mirroring the pre-check-then-execute pattern used in `blockchain/vm/interpreter.go`. Additionally, review precompile computation-cost constants (especially BLS12-381 multi-exp/pairing and legacy `bigModExp` pricing) to ensure the standalone per-call cost cannot exceed the full transaction budget in one shot even under this fix, e.g. by imposing a hard per-call cap or splitting cost accounting more granularly for large-input precompiles.

### Proof of Concept
1. Deploy or call an EOA-to-precompile transaction that directly `CALL`s the BLS12-381 `G2MultiExp` precompile (address per Kaia's precompile map) with an input containing many `(G2 point, scalar)` pairs (each pair is 288 bytes), sized so that `k = len(input)/288` is large.
2. Choose `k` such that `gas = k * Bls12381G2MulGas * discount / 1000` stays within the sender's affordable gas/transaction gas limit, while `computationCost = k * Bls12381G2MulComputationCost(=1,125,000) * discount / 1000` alone exceeds `params.OpcodeComputationCostLimitCancun` (150,000,000).
3. Submit the transaction. `RunPrecompiledContract` (via `run()` in `blockchain/vm/evm.go:85-97`) executes the full multi-exponentiation before ever checking `evm.opcodeComputationCostSum` against the limit, so every node executing this transaction spends CPU time proportional to `k`, exceeding the intended per-tx computation ceiling in a single call — regardless of the fact that on the *next* opcode dispatch the interpreter will finally detect and abort further execution.

Note: I was not able to execute this PoC in a live node/benchmark to measure exact wall-clock CPU time versus the configured limits, since I only have read access to the repository index (no execution environment). The discrepancy in enforcement logic between `blockchain/vm/interpreter.go` (pre-check) and `blockchain/vm/evm.go`'s `run()` (no check, post-hoc accounting only) is confirmed directly from the source, but real-world exploitability depends on actual precompile execution time per unit of `computationCost`/gas on production hardware, which should be validated experimentally by a Devin session with code-execution access.

### Citations

**File:** blockchain/vm/interpreter.go (L255-266)
```go
		// Static portion of gas
		cost = operation.constantGas // For tracing
		if !contract.UseGas(operation.constantGas) {
			return nil, kerrors.ErrOutOfGas
		}

		// We limit tx's execution time using the sum of computation cost of opcodes.
		in.evm.opcodeComputationCostSum += operation.computationCost
		if in.evm.opcodeComputationCostSum > in.evm.Config.ComputationCostLimit {
			return nil, ErrOpcodeComputationCostLimitReached
		}
		ccOpcode = operation.computationCost
```

**File:** blockchain/vm/evm.go (L73-101)
```go
// run runs the given contract and takes care of running precompiles with a fallback to the byte code interpreter.
func run(evm *EVM, contract *Contract, input []byte) ([]byte, error) {
	if contract.CodeAddr != nil {
		precompiles := evm.GetPrecompiledContractMap(contract.CallerAddress)
		if p := precompiles[*contract.CodeAddr]; p != nil {
			///////////////////////////////////////////////////////
			// OpcodeComputationCostLimit: The below code is commented and will be usd for debugging purposes.
			//var startTime time.Time
			//if opDebug {
			//	startTime = time.Now()
			//}
			///////////////////////////////////////////////////////
			ret, computationCost, err := RunPrecompiledContract(p, input, contract, evm) // TODO-Klaytn-Issue615
			///////////////////////////////////////////////////////
			// OpcodeComputationCostLimit: The below code is commented and will be usd for debugging purposes.
			//if opDebug {
			//	//fmt.Println("running precompiled contract...", "addr", contract.CodeAddr.String(), "computationCost", computationCost)
			//	elapsedTime := uint64(time.Since(startTime).Nanoseconds())
			//	addr := int(contract.CodeAddr.Bytes()[19])
			//	precompiledCnt[addr] += 1
			//	precompiledTime[addr] += elapsedTime
			//}
			///////////////////////////////////////////////////////
			evm.opcodeComputationCostSum += computationCost
			return ret, err
		}
	}
	return evm.interpreter.Run(contract, input)
}
```

**File:** blockchain/vm/contracts.go (L84-92)
```go
// RunPrecompiledContract runs and evaluates the output of a precompiled contract.
func RunPrecompiledContract(p PrecompiledContract, input []byte, contract *Contract, evm *EVM) (ret []byte, computationCost uint64, err error) {
	gas, computationCost := p.GetRequiredGasAndComputationCost(input)
	if contract.UseGas(gas) {
		ret, err = p.Run(input, contract, evm)
		return ret, computationCost, err
	}
	return nil, computationCost, kerrors.ErrOutOfGas
}
```

**File:** blockchain/vm/contracts.go (L1082-1100)
```go
// GetRequiredGasAndComputationCost returns the gas required to execute the pre-compiled contract.
func (c *bls12381G2MultiExp) GetRequiredGasAndComputationCost(input []byte) (uint64, uint64) {
	// Calculate G2 point, scalar value pair length
	k := len(input) / 288
	if k == 0 {
		// Return 0 gas for small input length
		return 0, 0
	}
	// Lookup discount value for G2 point, scalar value pair length
	var discount uint64
	if dLen := len(params.Bls12381G2MultiExpDiscountTable); k < dLen {
		discount = params.Bls12381G2MultiExpDiscountTable[k-1]
	} else {
		discount = params.Bls12381G2MultiExpDiscountTable[dLen-1]
	}
	// Calculate gas and return the result
	return (uint64(k) * params.Bls12381G2MulGas * discount) / 1000,
		(uint64(k) * params.Bls12381G2MulComputationCost * discount) / 1000
}
```
