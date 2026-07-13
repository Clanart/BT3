### Title
Nil Pointer Dereference Panic via `eth_call` StateOverride `balance:null` — (`rpc/types/types.go`)

### Summary

`OverrideAccount.Balance` is typed as `**hexutil.Big`. Go's `encoding/json` package, when it encounters a JSON `null` for a `**T` field, allocates the outer pointer (leaving it non-nil) while setting the inner `*T` to nil. `StateOverride.Apply` guards only on the outer pointer, then unconditionally dereferences the inner pointer and passes it to `uint256.MustFromBig`, which panics on a nil `*big.Int`. Any unprivileged caller can trigger this via `eth_call` with `stateOverrides: {"<addr>": {"balance": null}}`.

---

### Finding Description

`OverrideAccount.Balance` is declared as `**hexutil.Big`: [1](#0-0) 

`StateOverride.Apply` guards with `account.Balance != nil` (outer pointer check only), then immediately dereferences the inner pointer: [2](#0-1) 

Go's `encoding/json` behavior for `**T` with a JSON `null` value is well-defined: the outer `*(*hexutil.Big)` is allocated (non-nil), and the inner `*hexutil.Big` is set to nil. This is the standard Go idiom for distinguishing "field absent" (outer nil) from "field explicitly null" (outer non-nil, inner nil). The guard at line 100 passes because the outer pointer is non-nil. Line 101 then evaluates `(*big.Int)(*account.Balance)` where `*account.Balance` is a nil `*hexutil.Big`, producing a nil `*big.Int`. Line 102 passes that nil to `uint256.MustFromBig`, which calls `(*big.Int).Sign()` on the nil receiver, causing a nil pointer dereference panic.

By contrast, `SimOverrideAccount.Balance` uses a single `*hexutil.Big`: [3](#0-2) 

`SimStateOverride.Apply` is therefore not affected — a `null` JSON value simply leaves the single pointer nil, and the `account.Balance != nil` guard correctly skips it.

---

### Impact Explanation

The JSON-RPC server and the Ethermint node run in the same process. An unrecovered panic in a goroutine serving an `eth_call` request crashes the entire node process, halting the chain from that node's perspective. Because `eth_call` is a public, unauthenticated endpoint, any external caller can trigger this with a single crafted request. This satisfies the "Public JSON-RPC path exposes a reachable route to chain halt" impact category.

---

### Likelihood Explanation

The exploit requires no authentication, no funds, no prior state, and no special knowledge beyond the standard `eth_call` JSON-RPC interface. The payload is a single JSON object. The double-pointer `**hexutil.Big` pattern is uncommon and the nil-inner-pointer case is easy to miss in code review. No existing guard or middleware prevents the `null` value from reaching `StateOverride.Apply`.

---

### Recommendation

Replace the double-pointer `**hexutil.Big` with a single `*hexutil.Big` (matching `SimOverrideAccount.Balance`), or add an explicit nil-inner-pointer guard before dereferencing:

```go
// Option A: single pointer (preferred, matches SimOverrideAccount)
Balance *hexutil.Big `json:"balance"`

// Option B: guard if double pointer is intentionally kept
if account.Balance != nil && *account.Balance != nil {
    balance := (*big.Int)(*account.Balance)
    db.SetBalance(addr, *uint256.MustFromBig(balance))
}
```

---

### Proof of Concept

```bash
curl -X POST http://localhost:8545 \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc":"2.0","method":"eth_call","id":1,
    "params":[
      {"from":"0x0000000000000000000000000000000000000001","to":"0x0000000000000000000000000000000000000002"},
      "latest",
      {"0x0000000000000000000000000000000000000001":{"balance":null}}
    ]
  }'
```

Expected (vulnerable): node process panics with a nil pointer dereference inside `uint256.MustFromBig` called from `StateOverride.Apply`.

The root cause is at:
- `rpc/types/types.go:173` — `Balance **hexutil.Big` double pointer declaration
- `rpc/types/types.go:100–102` — outer-only nil guard followed by unconditional inner dereference and `MustFromBig` call [2](#0-1)

### Citations

**File:** rpc/types/types.go (L100-103)
```go
		if account.Balance != nil {
			balance := (*big.Int)(*account.Balance)
			db.SetBalance(addr, *uint256.MustFromBig(balance))
		}
```

**File:** rpc/types/types.go (L170-176)
```go
type OverrideAccount struct {
	Nonce     *hexutil.Uint64              `json:"nonce"`
	Code      *hexutil.Bytes               `json:"code"`
	Balance   **hexutil.Big                `json:"balance"`
	State     *map[common.Hash]common.Hash `json:"state"`
	StateDiff *map[common.Hash]common.Hash `json:"stateDiff"`
}
```

**File:** rpc/types/simulate.go (L98-105)
```go
type SimOverrideAccount struct {
	Nonce            *hexutil.Uint64             `json:"nonce"`
	Code             *hexutil.Bytes              `json:"code"`
	Balance          *hexutil.Big                `json:"balance"`
	State            map[common.Hash]common.Hash `json:"state"`
	StateDiff        map[common.Hash]common.Hash `json:"stateDiff"`
	MovePrecompileTo *common.Address             `json:"movePrecompileToAddress"`
}
```
