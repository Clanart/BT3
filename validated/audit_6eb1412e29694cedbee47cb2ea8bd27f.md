### Title
Integer underflow/out-of-bounds slice panic in `bigModExp.Run` via crafted MODEXP precompile input - (File: `blockchain/vm/contracts.go`)

### Summary
Kaia's `bigModExp` precompile (addresses `0x05` and `0xf5`, i.e. Byzantium/pre-EIP-7823 variants) accepts attacker-controlled `baseLen`/`expLen`/`modLen` fields that are truncated to `uint64` without the size-cap enforced only for `eip7823` (`0xf6`). A crafted MODEXP call input can make `getData(input, baseLen, expLen)` compute `start == length` and `size` chosen so that `start+size` wraps around `uint64`, producing `end < start` and triggering a Go slice-bounds panic in `data[start:end]`.

### Finding Description
`bigModExp.Run` reads three 256-bit length fields and truncates them to `uint64` via `.Uint64()`, which silently drops the high bits for values that don't fit in 64 bits: [1](#0-0) 

The size cap that would reject huge lengths is only applied when `c.eip7823` is true: [2](#0-1) 

For the legacy precompile at `0x05` (`eip2565:false, eip7883:false`) and `0xf5` (`eip2565:true, eip7883:false`), `eip7823` is `false`, so `inputLenOverflow` and `max(baseLen, expLen, modLen) > 1024` are never checked, leaving `baseLen`, `expLen`, `modLen` fully attacker-controlled `uint64` values: [3](#0-2) [4](#0-3) 

`Run` then slices the input using these lengths: [5](#0-4) 

`getData` is documented as "overflow safe," but the overflow-safety only covers `start > length` capping — it does not protect against `start + size` overflowing `uint64` when `start == length`: [6](#0-5) 

Concretely: choose `baseLen` equal to the actual remaining input length (`length`) so the `start > length` cap in `getData` is not triggered (`start == length`, unchanged). Then set `expLen` to a value such that `start + expLen` (mod 2^64) wraps to a value smaller than `start`. `end := min(start+size, length)` then evaluates to that wrapped, smaller value, so `end < start`, and `data[start:end]` panics with "slice bounds out of range" — a genuine integer-overflow-induced crash, directly analogous to the Frontier MODEXP integer underflow (CVE-2022-21685).

### Impact Explanation
A single, unprivileged, unauthenticated transaction (or `eth_call`/RPC) invoking precompile `0x05` or `0xf5` with a crafted MODEXP input can trigger a Go runtime panic inside EVM execution. If this panic is not recovered by an outer `defer/recover` (I was not able to locate any `recover()` call within the `blockchain/vm` package covering precompile execution in this repo — only a match in an unrelated test file), it can crash the node process executing the transaction, i.e. a public-RPC-reachable and block-execution-reachable denial-of-service. This is the same bug class as the original Frontier advisory (node crash / OOG), reachable here by any transaction sender or public RPC caller who can get a transaction/call routed to address `0x05`/`0xf5`.

### Likelihood Explanation
High likelihood of triggering the panic path given attacker control over all three length fields and no `eip7823`-style cap for the legacy precompile addresses; the crafted input requires no special privileges — just a plain transaction or `eth_call`. Whether this actually crashes a running node (vs. being caught by a `recover()` elsewhere in the call stack, e.g. in the block processor or RPC handler) could not be conclusively confirmed within the available context; no recovery mechanism was found in `blockchain/vm`, but higher-level recovery in the tx pool/miner/RPC layers was not fully ruled out due to remaining tool-call budget.

### Recommendation
Apply the same defensive checks used for `eip7823` universally (or at least bound `getData`'s `start+size` addition with overflow checking) regardless of fork flags, e.g. cap `baseLen`/`expLen`/`modLen` to sane bounds (or use saturating/overflow-checked addition) before slicing in `bigModExp.Run`, and/or add explicit `recover()` around precompile execution in `RunPrecompiledContract` to guarantee a bad precompile input degrades to a normal execution error rather than a process crash.

### Proof of Concept
1. Construct a MODEXP call to address `0x05` (or `0xf5`) with input where:
   - `baseLen` (bytes[0:32]) = actual remaining input length `L` after the 96-byte header (so `start == length` in the `exp` slice call, bypassing the `start > length` cap).
   - `expLen` (bytes[32:64]) = a `uint64` value `E` chosen so that `(L + E) mod 2^64 < L` (e.g., `E = 2^64 - L + k` for small `k < L`).
   - `modLen` arbitrary.
2. Submit as a transaction (or `eth_call`) targeting `0x05`/`0xf5` with this input.
3. During `Run`, the call `getData(input, baseLen, expLen)` computes `start = L`, `end = min((L+E) mod 2^64, L) = k < L`, and executes `data[L:k]`, panicking with "slice bounds out of range."

### Citations

**File:** blockchain/vm/contracts.go (L456-464)
```go
	var (
		baseLenBig       = new(big.Int).SetBytes(getData(input, 0, 32))
		expLenBig        = new(big.Int).SetBytes(getData(input, 32, 32))
		modLenBig        = new(big.Int).SetBytes(getData(input, 64, 32))
		baseLen          = baseLenBig.Uint64()
		expLen           = expLenBig.Uint64()
		modLen           = modLenBig.Uint64()
		inputLenOverflow = max(baseLenBig.BitLen(), expLenBig.BitLen(), modLenBig.BitLen()) > 64
	)
```

**File:** blockchain/vm/contracts.go (L470-473)
```go
	// enforce size cap for inputs
	if c.eip7823 && (inputLenOverflow || max(baseLen, expLen, modLen) > 1024) {
		return nil, errors.New("one or more of base/exponent/modulus length exceeded 1024 bytes")
	}
```

**File:** blockchain/vm/contracts.go (L478-483)
```go
	// Retrieve the operands and execute the exponentiation
	var (
		base = new(patched_big.Int).SetBytes(getData(input, 0, baseLen))
		exp  = new(patched_big.Int).SetBytes(getData(input, baseLen, expLen))
		mod  = new(patched_big.Int).SetBytes(getData(input, baseLen+expLen, modLen))
	)
```

**File:** blockchain/vm/precompiles.go (L54-65)
```go
var PrecompiledContractsByzantium = map[common.Address]PrecompiledContract{
	common.BytesToAddress([]byte{1}):  &ecrecover{},
	common.BytesToAddress([]byte{2}):  &sha256hash{},
	common.BytesToAddress([]byte{3}):  &ripemd160hash{},
	common.BytesToAddress([]byte{4}):  &dataCopy{},
	common.BytesToAddress([]byte{5}):  &bigModExp{eip2565: false, eip7823: false, eip7883: false},
	common.BytesToAddress([]byte{6}):  &bn256AddByzantium{},
	common.BytesToAddress([]byte{7}):  &bn256ScalarMulByzantium{},
	common.BytesToAddress([]byte{8}):  &bn256PairingByzantium{},
	common.BytesToAddress([]byte{9}):  &vmLog{},
	common.BytesToAddress([]byte{10}): &feePayer{},
	common.BytesToAddress([]byte{11}): &validateSender{},
```

**File:** blockchain/vm/contracts_test.go (L75-77)
```go
	common.BytesToAddress([]byte{5}):    &bigModExp{eip2565: false, eip7883: false},
	common.BytesToAddress([]byte{0xf5}): &bigModExp{eip2565: true, eip7883: false},
	common.BytesToAddress([]byte{0xf6}): &bigModExp{eip2565: true, eip7883: true},
```

**File:** blockchain/vm/common.go (L58-67)
```go
// getData returns a slice from the data based on the start and size and pads
// up to size with zero's. This function is overflow safe.
func getData(data []byte, start uint64, size uint64) []byte {
	length := uint64(len(data))
	if start > length {
		start = length
	}
	end := min(start+size, length)
	return common.RightPadBytes(data[start:end], int(size))
}
```
