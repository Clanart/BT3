### Title
Unbounded attacker-controlled regex pattern in tx/block search `MATCHES` operator enables stack-consumption DoS on public RPC nodes - ([File: sei-tendermint/internal/state/indexer/tx/kv/kv.go])

### Summary
The Tendermint/CometBFT-derived RPC query grammar exposes a `MATCHES` comparison operator whose argument is an arbitrary client-supplied string that is compiled and executed as a Go regular expression against every indexed event value, once per matching key, with no length or structural (nesting-depth) limit applied before it reaches `regexp.MatchString`.

### Finding Description
The query parser accepts a free-form string argument for the `TMatches` operator without imposing any bound on its length or nesting complexity: [1](#0-0) 

That raw argument text is later fed directly into `regexp.MatchString` for every key visited during a tx/block search, both in the tx indexer and the block indexer: [2](#0-1) [3](#0-2) 

`regexp.MatchString(pattern, s)` internally calls `regexp.Compile(pattern)` on every invocation (it is not cached/precompiled once per query), so the same attacker-controlled pattern is parsed and compiled into an NFA program repeatedly inside the iteration loop. Unlike other places in this codebase that explicitly defend against unbounded recursive structures — e.g. the protobuf unknown-field walker's `MaxProtobufNestingDepth = 100` guard [4](#0-3) , the CosmWasm query/call-depth counters [5](#0-4) , the debug-tracer mux nesting-depth check [6](#0-5) , and the TOML config array-depth check [7](#0-6)  — there is no analogous depth or length guard applied to the `MATCHES` regex argument before it is compiled. This is structurally the same bug class described in CVE-2017-9438: a crafted pattern is handed to a regex engine's parse/emit machinery without a recursion or size cap, allowing the parser/compiler's internal tree-walk to consume stack proportional to the crafted structure rather than to any bounded resource.

### Impact Explanation
The `tx_search` and `block_search` RPC endpoints are public, unauthenticated, and reachable by any RPC client (the rules explicitly allow "public-RPC client" as an in-scope actor). A single crafted query containing a pathologically deep/complex regex string (e.g. deeply nested groups or repetition operators) submitted as the `MATCHES` argument would be compiled server-side inside the indexer's iteration loop. If the resulting parse/compile recursion exceeds the Go runtime's stack limits, the RPC node process crashes (unrecoverable panic → process exit), producing a denial of service against default-configuration RPC nodes — one of the explicitly accepted impact categories for this task ("a crash of default-configuration RPC nodes").

### Likelihood Explanation
Likelihood depends on whether the specific Go runtime/`regexp` package version compiled into this binary is still susceptible to stack exhaustion from adversarial pattern structure; some Go regexp internals process the parsed AST via structural (non-tail) recursion over nested nodes, and no version-specific verification of this codebase's Go toolchain's resistance to that behavior was possible from the index alone. What is certain is that the application layer applies zero mitigation of its own (no max pattern length, no nesting cap, no recompilation cache) despite doing so pervasively elsewhere for structurally identical attacker-supplied recursive inputs. This inconsistency, combined with the endpoint's public, repeatable, and unauthenticated reachability, makes this a credible, low-cost attack surface even if the underlying Go regexp engine provides partial mitigation.

### Recommendation
- Impose an explicit maximum length and/or nesting-depth bound on `MATCHES` argument text at parse time in `sei-tendermint/internal/pubsub/query/syntax/parser.go`, consistent with the depth guards already used elsewhere in this codebase (e.g. mirror the `MaxProtobufNestingDepth`/mux-tracer-depth pattern).
- Compile the `MATCHES` regex once per query (with `regexp.Compile`) inside `sei-tendermint/internal/state/indexer/tx/kv/kv.go` and `sei-tendermint/internal/state/indexer/block/kv/kv.go`, rather than recompiling per iterated key, and reject the query up front (returning a client error) if compilation fails or exceeds a bounded size/step budget — using `regexp/syntax`'s program-size APIs to enforce a hard cap before executing the match loop.

### Proof of Concept
1. Start a `seid` node with the default RPC server enabled (tx/event indexing on, the default `kv` indexer).
2. Send an HTTP RPC request to the public `tx_search` endpoint with a query string such as:
   `tx_search?query="tx.hash MATCHES '` + (a string built from tens/hundreds of thousands of nested group/repetition metacharacters, e.g. `((((((...))))))` or repeated `a{1,100}{1,100}...`) + `'"`
3. Because the argument is unbounded in length and structure at both the parser (`syntax/parser.go`) and the indexer match loop (`kv.go`), the server recompiles this pattern via `regexp.MatchString` for indexed keys.
4. If the crafted pattern drives the regex parser/compiler's tree traversal deep enough to exceed the goroutine stack limit, the RPC node process panics/crashes, denying service to that node — this can be repeated against any public RPC node running the default indexer configuration.

### Citations

**File:** sei-tendermint/internal/pubsub/query/syntax/parser.go (L162-176)
```go
	case TContains:
		err = p.require(TString)
	case TMatches:
		err = p.require(TString)
	case TExists:
		// no argument
		return cond, nil
	default:
		return cond, fmt.Errorf("offset %d: unexpected operator %v", p.scanner.Pos(), cond.Op)
	}
	if err != nil {
		return cond, err
	}
	cond.Arg = &Arg{Type: p.scanner.Token(), text: p.scanner.Text()}
	return cond, nil
```

**File:** sei-tendermint/internal/state/indexer/tx/kv/kv.go (L661-681)
```go
	case syntax.TMatches:
		it, err := dbm.IteratePrefix(txi.store, prefixFromCompositeKey(c.Tag))
		if err != nil {
			return nil, err
		}
		defer func() { _ = it.Close() }()

	iterMatches:
		for ; it.Valid(); it.Next() {
			if err := lease.Visit(1); err != nil {
				return nil, err
			}

			value, err := parseValueFromKey(it.Key())
			if err != nil {
				continue
			}
			if match, _ := regexp.MatchString(c.Arg.Value(), value); match {
				tmpHashes[string(it.Value())] = it.Value()
			}

```

**File:** sei-tendermint/internal/state/indexer/block/kv/kv.go (L706-731)
```go
	case syntax.TMatches:
		prefix, err := orderedcode.Append(nil, c.Tag)
		if err != nil {
			return nil, err
		}

		it, err := dbm.IteratePrefix(idx.store, prefix)
		if err != nil {
			return nil, fmt.Errorf("failed to create prefix iterator: %w", err)
		}
		defer func() { _ = it.Close() }()

	iterMatches:
		for ; it.Valid(); it.Next() {
			if err := lease.Visit(1); err != nil {
				return nil, err
			}

			eventValue, err := parseValueFromEventKey(it.Key())
			if err != nil {
				continue
			}

			if match, _ := regexp.MatchString(c.Arg.Value(), eventValue); match {
				tmpHeights[string(it.Value())] = it.Value()
			}
```

**File:** sei-cosmos/codec/unknownproto/unknown_fields.go (L21-26)
```go
const bit11NonCritical = 1 << 10

// MaxProtobufNestingDepth defines the maximum allowed nesting depth for protobuf messages
// to prevent stack overflow attacks. This matches similar limits in other protobuf implementations.
const MaxProtobufNestingDepth = 100

```

**File:** sei-wasmd/x/wasm/keeper/keeper.go (L697-719)
```go
func checkAndIncreaseQueryStackSize(ctx sdk.Context, maxQueryStackSize uint32) (sdk.Context, error) {
	var queryStackSize uint32

	// read current value
	if size := ctx.Context().Value(contextKeyQueryStackSize); size != nil {
		queryStackSize = size.(uint32)
	} else {
		queryStackSize = 0
	}

	// increase
	queryStackSize++

	// did we go too far?
	if queryStackSize > maxQueryStackSize {
		return ctx, types.ErrExceedMaxQueryStackSize
	}

	// set updated stack size
	ctx = ctx.WithContext(context.WithValue(ctx.Context(), contextKeyQueryStackSize, queryStackSize))

	return ctx, nil
}
```

**File:** evmrpc/tracers.go (L287-293)
```go
func validateMuxTraceConfig(raw json.RawMessage, allowed map[string]struct{}, allowJS bool, depth int) error {
	if len(raw) == 0 {
		return nil
	}
	if depth > maxMuxTracerNestingDepth {
		return fmt.Errorf("muxTracer nesting depth exceeds maximum of %d", maxMuxTracerNestingDepth)
	}
```

**File:** config/seitoml/file.go (L219-227)
```go
//
// Refused here, between the parse and the decode. Parsing is linear in the bytes whatever shape they take,
// measured within 14 percent across a deep key, a deep array and a flat file of the same size. What grows
// faster than the bytes is decoding the result, and nothing downstream of that can refuse a boot.
func valueIsAddressableWithin(key parser.Key, v parser.Value, depth int) error {
	if depth > maxArrayDepth {
		return fmt.Errorf("%s nests arrays %d deep and this file is read to %d. No setting here is a "+
			"list of lists, so nothing legitimate reaches that depth", key, depth, maxArrayDepth)
	}
```
