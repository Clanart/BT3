### Title
Integer overflow/underflow in `consoleLog.decode` causes attacker-controlled negative slice index panic, crashing all nodes executing the transaction - (File: blockchain/vm/contracts.go)

### Summary
The `consoleLog` precompiled contract, which emulates Hardhat's `console.log` for local networks, decodes ABI-encoded parameters directly from attacker-supplied EVM call data without validating that the derived offsets are non-negative. This mirrors the CVE-2016-4344 bug class: an integer-overflow/underflow on a length/offset value derived from untrusted input, feeding directly into a low-level memory/slice operation without a proper bounds check, causing memory-safety-adjacent behavior (in Go, an unrecoverable runtime panic) instead of PHP's heap overflow.

### Finding Description
For `StringTy` and `BytesTy` console-log arguments, the decoder computes a `start` offset by reading 32 bytes of caller-controlled `params` into a `big.Int` and truncating it via `.Int64()`: [1](#0-0) 

`big.Int.Int64()` on a value derived from an arbitrary 256-bit word simply reinterprets the low 64 bits as a signed `int64` — if the caller supplies a value whose low 64 bits have the sign bit set (e.g. `0x8000000000000000...`), `start` becomes **negative**. The subsequent bounds check:

```go
if len(params) < start+registerSize {
    return nil, errors.New("input too short")
}
```

is bypassed because `len(params)` (always ≥ 0) is never less than a negative number, so execution proceeds to `params[start : start+registerSize]` with a negative index. Go slicing with a negative index is not a bounds-checked "too short" condition — it is an **invalid slice bounds** condition that triggers an unrecoverable runtime panic (`slice bounds out of range`). The identical unchecked pattern exists for `BytesTy`: [2](#0-1) 

This precompile is invoked directly from `Run`, itself called by the EVM interpreter for any `CALL`/`STATICCALL` targeting the console-log precompile address that any deployed contract can trigger: [3](#0-2) 

No `defer/recover` wraps precompile execution in the interpreter/call path in `blockchain/vm`, so the panic is not contained and propagates up through the transaction/block-processing goroutine.

### Impact Explanation
Because every full node (and every consensus node) that executes/re-executes a block containing such a transaction runs the same deterministic decode logic, a single crafted transaction can crash the Go process on all nodes that process it — including nodes validating blocks during consensus and RPC nodes replaying transactions for tracing/mempool checks. This is a network-wide denial-of-service triggerable by an ordinary, unprivileged transaction sender/contract deployer, analogous to the DoS impact described in CVE-2016-4344.

### Likelihood Explanation
Likelihood is high: any address can deploy a small contract that performs a low-level `call`/`staticcall` to the console-log precompile address with hand-crafted ABI parameters, and any transaction invoking it will be executed by every node that processes that block or transaction (mempool validation, block execution, tracing, RPC simulation). No special privileges, governance parameters, or race conditions are required — only the ability to submit a transaction.

### Recommendation
Add explicit non-negativity and upper-bound checks after truncating `start`/`size` from `big.Int` before using them as slice indices (e.g., verify `0 <= start <= len(params)` and `0 <= size <= len(params)-start-registerSize`), and guard `Bytes1Ty..Bytes32Ty` similarly. Additionally, wrap precompiled-contract execution (or at minimum the EVM `Call`/`Run` dispatch loop) with a `defer/recover` that converts panics into an `error` (`ErrExecutionReverted`) instead of allowing the panic to crash the node process.

### Proof of Concept
1. Deploy a contract that performs a raw `staticcall`/`call` to the console-log precompile address with a selector matching a registered `common.ConsoleLogSignatures` entry containing a `StringTy` or `BytesTy` argument.
2. Craft the 32-byte "offset" word for that argument so that, interpreted via `big.NewInt(0).SetBytes(...).Int64()`, it yields a negative `int64` (e.g. set the high bit of the low 8 bytes, such as `...800000000000000000`).
3. Send the transaction; when any node's EVM interpreter executes `consoleLog.decode`, `params[start : start+registerSize]` with negative `start` triggers `panic: runtime error: slice bounds out of range`, which is unrecovered and crashes the node process during block/transaction execution.

### Citations

**File:** blockchain/vm/contracts.go (L1379-1385)
```go
func (c *consoleLog) Run(input []byte, contract *Contract, evm *EVM) ([]byte, error) {
	decoded, _ := c.toLogString(input)
	if decoded != "" {
		logger.Debug(decoded)
	}
	return nil, nil
}
```

**File:** blockchain/vm/contracts.go (L1439-1451)
```go
		case common.StringTy:
			if len(params) < pos+registerSize {
				return nil, errors.New("input too short")
			}
			start := int(big.NewInt(0).SetBytes(params[pos : pos+registerSize]).Int64())
			if len(params) < start+registerSize {
				return nil, errors.New("input too short")
			}
			size := int(big.NewInt(0).SetBytes(params[start : start+registerSize]).Int64())
			if len(params) < start+size+registerSize {
				return nil, errors.New("input too short")
			}
			res = append(res, string(params[start+registerSize:start+size+registerSize]))
```

**File:** blockchain/vm/contracts.go (L1457-1469)
```go
		case common.BytesTy:
			if len(params) < pos+registerSize {
				return nil, errors.New("input too short")
			}
			start := int(big.NewInt(0).SetBytes(params[pos : pos+registerSize]).Int64())
			if len(params) < start+registerSize {
				return nil, errors.New("input too short")
			}
			size := int(big.NewInt(0).SetBytes(params[start : start+registerSize]).Int64())
			if len(params) < start+size+registerSize {
				return nil, errors.New("input too short")
			}
			res = append(res, common.Bytes2Hex(params[start+registerSize:start+size+registerSize]))
```
