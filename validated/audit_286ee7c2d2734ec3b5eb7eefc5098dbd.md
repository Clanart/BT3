### Title
Out-of-bounds slice panic in `consoleLog.decode` due to unchecked integer overflow in offset/size arithmetic - ([File: blockchain/vm/contracts.go])

### Summary
`consoleLog.decode`, the argument decoder for the `CONSOLE_LOG` precompile, parses attacker-controlled `StringTy`/`BytesTy` arguments by reading a 256-bit `start` offset and a 256-bit `size` value directly out of the call-data bytes and truncating them to Go `int` via `big.Int.Int64()`, then bounds-checking with plain (non-overflow-safe) integer addition before slicing `params`. This mirrors the Bento4 `AP4_Dec3Atom` bug class: a length/size field taken from untrusted input is used to index/copy a buffer without properly validating against overflow, leading to an out-of-bounds access. In Go the analogous consequence is not heap corruption but a runtime slice-bounds panic, i.e. a Denial of Service.

### Finding Description
In `blockchain/vm/contracts.go`, the `StringTy` and `BytesTy` cases of `consoleLog.decode` do: [1](#0-0) [2](#0-1) 

Both `start` and `size` are derived from `big.NewInt(0).SetBytes(...).Int64()`, converting an arbitrary 256-bit attacker-supplied word into an `int64`. Per Go's `math/big` documentation, `Int64()` is undefined/wraps when the value doesn't fit in an `int64`, so a crafted 32-byte word can produce a large, or even negative, `start`/`size` after conversion. The subsequent bound checks (`len(params) < start+registerSize`, `len(params) < start+size+registerSize`) use plain machine-width integer addition with no overflow protection, so a crafted `start`/`size` pair can make `start+size+registerSize` wrap to a small or negative number, causing the check to pass even though the actual slice bounds `params[start+registerSize : start+size+registerSize]` are invalid (negative or far out of range). This results in a slice-bounds-out-of-range runtime panic when the slice expression executes.

This is directly analogous to `AP4_Dec3Atom::AP4_Dec3Atom` in Bento4, where a length field read from untrusted media data is used without adequate bounds validation, leading to memory corruption/DoS. Here the parsed field is the ABI-encoded `console.log` argument (offset/length), read from EVM call-data, and used without overflow-safe validation, leading to a crash-inducing panic instead of memory corruption because Go slices are bounds-checked at runtime.

Contrast this with the codebase's own hardened analog, `accounts/abi/unpack.go`'s `lengthPrefixPointsTo`, which performs the equivalent offset/length arithmetic using `big.Int` throughout precisely to avoid this overflow class: [3](#0-2) 
`consoleLog.decode` does not follow this safe pattern.

### Impact Explanation
The `CONSOLE_LOG` precompile is only registered when `evm.Config.UseConsoleLog` is enabled: [4](#0-3) 
I was not able to fully determine from the indexed code whether `UseConsoleLog` is enabled by default on any live Kaia network (mainnet/testnet) or is strictly a local/dev/test flag — this materially affects real-world impact and I could not verify it with the tools available. If enabled on a node that processes untrusted transactions, any contract deployer/caller can trigger the panic from a single transaction by invoking a contract that calls the console.log precompile with a crafted argument, causing the executing node to crash or panic during EVM execution — a Denial of Service consistent with the CVE's stated impact ("leading to a Denial of Service (DoS)").

### Likelihood Explanation
If `UseConsoleLog` is active, the trigger requires only a single contract call with attacker-chosen call-data (no special privileges, no p2p/consensus dependency) — reachable by any unprivileged transaction sender. Constructing the crafted 32-byte `start`/`size` words that force the overflow is straightforward given `Int64()`'s well-documented wraparound behavior. The main uncertainty is deployment scope of `UseConsoleLog`.

### Recommendation
- Perform offset/length arithmetic for `StringTy`/`BytesTy` decoding using `big.Int` (or explicit overflow-checked arithmetic) exactly as done in `accounts/abi/unpack.go`'s `lengthPrefixPointsTo`/`tuplePointsTo`, rejecting any offset/length whose value cannot be safely represented as a non-negative `int` within `len(params)`.
- Reject negative or implausibly large `start`/`size` values explicitly before use in slice expressions.
- Add `recover()` around precompile `Run` invocations (if not already present) as defense in depth against any residual panics.
- Confirm and, if necessary, restrict where `UseConsoleLog` can be enabled (e.g., disallow enabling it for nodes that process untrusted/public transactions).

### Proof of Concept
Craft `CONSOLE_LOG` call-data whose 4-byte selector matches a `common.ConsoleLogSignatures` entry with a `StringTy` or `BytesTy` argument, then set the corresponding "offset" word (`params[pos:pos+32]`) to a value such as `0x7FFFFFFFFFFFFFFF...` (or any 256-bit value whose low 64 bits, interpreted as signed, are large/negative) so that `big.NewInt(...).Int64()` returns a huge or negative `start`. Set the following "length" word similarly so that `start + size + registerSize` overflows back into a value ≤ `len(params)`, passing the bounds check in: [5](#0-4) 
Executing this call on a node with `evm.Config.UseConsoleLog` enabled triggers `params[start+registerSize : start+size+registerSize]` with an invalid range, causing a Go runtime "slice bounds out of range" panic during `consoleLog.Run` / `EVM` execution.

**Note:** I could not verify from the available index whether `UseConsoleLog` is enabled in any production Kaia network configuration; this should be confirmed before treating this as exploitable in production, since if the precompile is strictly test/dev-only, the finding would not meet the "reachable via public transaction/RPC" scope requirement.

### Citations

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

**File:** accounts/abi/unpack.go (L291-319)
```go
func lengthPrefixPointsTo(index int, output []byte) (start int, length int, err error) {
	bigOffsetEnd := big.NewInt(0).SetBytes(output[index : index+32])
	bigOffsetEnd.Add(bigOffsetEnd, common.Big32)
	outputLength := big.NewInt(int64(len(output)))

	if bigOffsetEnd.Cmp(outputLength) > 0 {
		return 0, 0, fmt.Errorf("abi: cannot marshal in to go slice: offset %v would go over slice boundary (len=%v)", bigOffsetEnd, outputLength)
	}

	if bigOffsetEnd.BitLen() > 63 {
		return 0, 0, fmt.Errorf("abi offset larger than int64: %v", bigOffsetEnd)
	}

	offsetEnd := int(bigOffsetEnd.Uint64())
	lengthBig := big.NewInt(0).SetBytes(output[offsetEnd-32 : offsetEnd])

	totalSize := big.NewInt(0)
	totalSize.Add(totalSize, bigOffsetEnd)
	totalSize.Add(totalSize, lengthBig)
	if totalSize.BitLen() > 63 {
		return 0, 0, fmt.Errorf("abi: length larger than int64: %v", totalSize)
	}

	if totalSize.Cmp(outputLength) > 0 {
		return 0, 0, fmt.Errorf("abi: cannot marshal in to go type: length insufficient %v require %v", outputLength, totalSize)
	}
	start = int(bigOffsetEnd.Uint64())
	length = int(lengthBig.Uint64())
	return
```

**File:** blockchain/vm/precompiles.go (L243-254)
```go
// GetPrecompiledContractMap returns the precompiled contract map for the given
// caller address, accounting for vmversion and the active fork rules.
// If console.log is enabled in the EVM config, the consoleLog precompile is
// included as well.
func (evm *EVM) GetPrecompiledContractMap(addr common.Address) map[common.Address]PrecompiledContract {
	precompiles := evm.getPrecompiledContractForVersion(addr)
	// If console.log is enabled, add console.log precompile too.
	if evm.Config.UseConsoleLog {
		precompiles[consoleLogContractAddress] = &consoleLog{}
	}
	return precompiles
}
```
