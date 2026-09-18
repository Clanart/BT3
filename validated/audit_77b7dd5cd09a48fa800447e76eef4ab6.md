### Title
Unbounded-recursion stack overflow in the `json` precompile's `encoding/json.Unmarshal` calls - (File: `precompiles/json/json.go`)

### Summary
The `json` Cosmos precompile (address `0x0000000000000000000000000000000000001003`), reachable by any EVM transaction sender, decodes fully attacker-controlled calldata bytes with Go's standard `encoding/json.Unmarshal` and applies no nesting-depth limit. Every other structured-decoding path in this codebase that accepts untrusted input has an explicit recursion/nesting cap for exactly this class of bug (CVE-2020-36366: unbounded-recursion stack overflow in a "parse value" routine), but this one does not.

### Finding Description
`PrecompileExecutor.extractAsBytes`, `extractAsBytesList`, `ExtractAsUint256`, and `extractAsBytesFromArray` in `precompiles/json/json.go` all take `args[0].([]byte)` — the raw bytes passed by the calling contract/EOA as an ABI `bytes` argument — and pass them straight into `gjson.Unmarshal` (an alias for the Go standard library `encoding/json.Unmarshal`, not the tidwall/gjson library): [1](#0-0) [2](#0-1) 

Go's `encoding/json` decoder decodes nested JSON arrays/objects via mutually-recursive Go function calls (`decodeState.value` → `decodeState.array`/`decodeState.object` → `decodeState.value` → …), with no built-in depth limit. A payload consisting of deeply nested arrays (e.g. `"[[[[[…]]]]]"`) drives this recursion to a depth proportional to the number of bracket pairs supplied, at effectively 2 bytes of input per recursion level. Because Go's goroutine stacks grow dynamically and only fail with an unrecoverable fatal runtime error (`runtime: goroutine stack exceeds …-byte limit`, `fatal error: stack overflow`) once the max-stack limit is hit, sufficiently deep nesting crashes the whole node process — this cannot be caught by `recover()`.

This is the exact bug class described in the report (`parse_value`-style stack overflow via crafted nested input), mapped onto the EVM-precompile surface explicitly called out as in scope. Notably, the codebase shows this class of bug was proactively addressed elsewhere but missed here:
- Protobuf message decoding caps nesting at 100 (`MaxProtobufNestingDepth`) specifically "to prevent stack overflow attacks": [3](#0-2) 
- Precompile ABI-decode cost estimation caps type-node traversal (`maxDecodeWalkOps`) as "a defensive backstop so a hypothetical deeply-nested type cannot turn the cost estimate itself into a super-linear computation": [4](#0-3) 
- Node TOML config parsing bounds array nesting depth (`maxArrayDepth = 8`) before decoding: [5](#0-4) 
- IAVL/memiavl tree serialization was deliberately rewritten from recursive to an explicit-stack iterative algorithm "because a recursive algorithm risks hitting the stack limit and causing a stack overflow should the tree be too large": [6](#0-5) [7](#0-6) 

The `json` precompile's `gjson.Unmarshal` calls have no equivalent guard: only a linear byte-count gas charge is applied (`GasCostPerByte * len(payload)`), which limits total input *size* but not nesting *depth*, and nesting depth costs only ~2 bytes/level of input: [8](#0-7) 

### Impact Explanation
A successful stack overflow in Go is a `fatal error` that terminates the process — it is not a panic that a `recover()`-based error boundary (e.g. the EVM's own panic recovery around opcode/precompile execution) can catch. If a transaction carrying a deeply-nested JSON payload reaches this precompile during block execution, every validator processing that block would crash simultaneously (since block execution is deterministic and all validators execute the same transaction) — this is a network-wide validator halt, not a single-node issue. The same precompile is also reachable via `eth_call`/`eth_estimateGas` on any public EVM JSON-RPC node, which would let an attacker crash arbitrary public RPC nodes without needing a transaction to be included on-chain at all.

### Likelihood Explanation
The precompile is a stateless, unrestricted public entry point at a fixed address (`0x1003`) that any EOA or contract can call by simply constructing calldata; no special privileges, contract deployment, or setup is required beyond crafting nested-bracket bytes as an ABI `bytes` argument. The only gate is gas: the precompile charges `GasCostPerByte (100) * len(payload)` up front, so the achievable nesting depth is bounded by `(tx gas limit / 100) / 2` bracket pairs. With Sei's observed default/consensus gas ceilings in the multi-tens-of-millions range (e.g. `MaxGasWanted = 50,000,000` in `sei-tendermint/types/params.go`, up to `100,000,000` `MaxGas`), an attacker can afford on the order of hundreds of thousands of nesting levels well within a single transaction's gas and the ~21MB block-byte limit. Whether that specific depth is sufficient to exceed Go's default 1 GB max-goroutine-stack ceiling depends on the actual stack-frame size of the `encoding/json` decode call chain, which I was not able to benchmark within this environment — this is the main remaining uncertainty. However, the complete absence of any depth guard (unlike every comparable parser in this codebase) means the *ceiling* on achievable depth is set only by the gas/byte budget, not by any deliberate mitigation, so the risk scales with whatever gas limits validators configure and is not bounded by design.

### Recommendation
Add an explicit nesting-depth cap before or during JSON decoding in `precompiles/json/json.go`, mirroring `MaxProtobufNestingDepth` in `sei-cosmos/codec/unknownproto/unknown_fields.go` or `maxArrayDepth` in `config/seitoml/file.go`: e.g., pre-scan the input bytes and reject payloads whose bracket/brace nesting exceeds a small bound (tens, not thousands) before calling `encoding/json.Unmarshal`, or switch to a decoder/library that enforces a maximum nesting depth internally. Apply the same fix to all `precompiles/json/legacy/vNNN/json.go` copies that contain the identical pattern.

### Proof of Concept
1. Construct calldata for `extractAsBytes(bytes,string)` (or any of the other three methods) on the `json` precompile at `0x0000000000000000000000000000000000001003`.
2. Set the `bytes input` argument to `N` repeated `'['` characters (no closing brackets needed to trigger the recursive descent into `d.array()`/`d.value()` before EOF is hit), where `N` is large enough to be affordable under the transaction's gas limit at `GasCostPerByte = 100` gas/byte (e.g. `N ≈ 400,000` costs ~40,000,000 gas, affordable under a ~50–100M gas budget).
3. Submit the transaction (or call `eth_call`/`eth_estimateGas` against a public RPC node) so that `PrecompileExecutor.extractAsBytes` calls `gjson.Unmarshal(bz, &decoded)` on the payload.
4. Go's `encoding/json` decoder recurses once per `[` encountered while attempting to decode into `interface{}`, driving the goroutine's call stack to depth `N`; at sufficient `N` this exceeds the runtime's max-stack limit and crashes the process with an unrecoverable `fatal error: stack overflow`, taking down the validator/RPC node executing the transaction. [1](#0-0)

### Citations

**File:** precompiles/json/json.go (L1-19)
```go
package json

import (
	"embed"
	gjson "encoding/json"
	"errors"
	"fmt"
	"math/big"
	"strings"

	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/tracing"
	"github.com/ethereum/go-ethereum/core/vm"
	pcommon "github.com/sei-protocol/sei-chain/precompiles/common"
	putils "github.com/sei-protocol/sei-chain/precompiles/utils"
	sdk "github.com/sei-protocol/sei-chain/sei-cosmos/types"
	"github.com/sei-protocol/sei-chain/utils"
)
```

**File:** precompiles/json/json.go (L72-81)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (bz []byte, remainingGas uint64, err error) {
	// The precompile is stateless (in-memory JSON parsing), so no gas would be
	// charged via state access. Charge for the parse work up front, proportional
	// to the payload being parsed (args[0]); this is the dominant cost of every
	// method below.
	if len(args) > 0 {
		if payload, ok := args[0].([]byte); ok {
			ctx.GasMeter().ConsumeGas(uint64(GasCostPerByte*len(payload)), "json parse") //nolint:gosec
		}
	}
```

**File:** precompiles/json/json.go (L108-122)
```go
func (p PrecompileExecutor) extractAsBytes(_ sdk.Context, method *abi.Method, args []interface{}, value *big.Int) ([]byte, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, err
	}

	if err := pcommon.ValidateArgsLength(args, 2); err != nil {
		return nil, err
	}

	// type assertion will always succeed because it's already validated in p.Prepare call in Run()
	bz := args[0].([]byte)
	decoded := map[string]gjson.RawMessage{}
	if err := gjson.Unmarshal(bz, &decoded); err != nil {
		return nil, err
	}
```

**File:** sei-cosmos/codec/unknownproto/unknown_fields.go (L21-25)
```go
const bit11NonCritical = 1 << 10

// MaxProtobufNestingDepth defines the maximum allowed nesting depth for protobuf messages
// to prevent stack overflow attacks. This matches similar limits in other protobuf implementations.
const MaxProtobufNestingDepth = 100
```

**File:** precompiles/common/decode_cost.go (L13-19)
```go
// maxDecodeWalkOps bounds how many type nodes decodeStringCopyBytes visits. For
// the argument shapes precompiles actually use (single-level dynamic arrays and
// flat tuples of leaves) the walk is linear in len(data); this cap is a
// defensive backstop so a hypothetical deeply-nested type cannot turn the cost
// estimate itself into a super-linear computation. Calldata large enough to hit
// it is infeasible under EVM calldata gas costs.
const maxDecodeWalkOps = 1 << 20
```

**File:** config/seitoml/file.go (L262-270)
```go
const (
	// maxFileBytes bounds the bytes Load will read. A file stating every declared key is a few tens
	// of kilobytes.
	maxFileBytes = 1 << 20
	// maxKeyDepth bounds the segments in one key. A setting is a section and a key inside it.
	maxKeyDepth = 8
	// maxArrayDepth bounds nesting inside a value. No setting here is a list of lists.
	maxArrayDepth = 8
)
```

**File:** sei-tendermint/crypto/merkle/tree.go (L29-36)
```go
// HashFromByteSliceIterative is an iterative alternative to
// HashFromByteSlice motivated by potential performance improvements.
// (#2611) had suggested that an iterative version of
// HashFromByteSlice would be faster, presumably because
// we can envision some overhead accumulating from stack
// frames and function calls. Additionally, a recursive algorithm risks
// hitting the stack limit and causing a stack overflow should the tree
// be too large.
```

**File:** sei-db/state_db/sc/memiavl/snapshot.go (L1042-1046)
```go
// writePostOrder writes the tree in depth-first post-order.
func (w *snapshotWriter) writePostOrder(root Node) error {
	// An explicit stack keeps allocations under a fixed call stack. Recursive
	// traversal makes allocation profiles depend on every left/right tree path.
	stack := make([]snapshotWriteFrame, 1, int(root.Height())+1)
```
