### Title
Silent octal misinterpretation of leading-zero amount strings in `sdk.NewIntFromString`/`ParseUint` used by the CosmWasm→bank coin bridge - (File: `sei-cosmos/types/int.go`)

### Summary
`sei-cosmos/types/int.go` parses arbitrary decimal-looking amount strings with Go's `big.Int.SetString(s, 0)`, which auto-detects numeric base from the string prefix. Any string beginning with `0` followed by digits `0-7` is silently reinterpreted as **octal**, not decimal — the exact bug class described in ALPINE-CVE-2021-29922 (unexpected octal interpretation of leading zeros). This routine backs `sdk.NewIntFromString`, which is used to convert externally supplied amount strings — most notably CosmWasm contract `Coin.Amount` values — into the `sdk.Int`/`sdk.Coin` that actually moves value on-chain.

### Finding Description
`newIntegerFromString` is the shared parser behind `sdk.Int.NewIntFromString`: [1](#0-0) 

`big.Int.SetString(s, 0)` uses base‑0 auto‑detection: a bare `0` prefix (without `x`/`b`) selects base 8. So the string `"0123"` is parsed as octal 0123 = decimal **83**, not decimal **123** — no error is returned, the conversion just silently produces a different, smaller magnitude value. `sdk.NewIntFromString` wraps this with only a bit-length overflow check, not a base/format check: [2](#0-1) 

`sdk.ParseUint` in `uint.go` has the identical pattern: [3](#0-2) 

The most concretely reachable, unprivileged, on-chain consumer of this parser is the CosmWasm→bank coin bridge, which converts a wasm-VM `Coin.Amount` string (produced by contract execution, e.g. `funds` on a `Wasm::Execute`/`Wasm::Instantiate` submessage or a `BankMsg::Send`) directly into the `sdk.Coin` that is actually transferred: [4](#0-3) 

Any CosmWasm contract (deployable and callable by any unprivileged user) that forwards an untrusted or attacker-influenced numeric string as a coin amount — rather than round-tripping it through a strict Rust `Uint128`/decimal validator before re-serializing — will have that value silently reinterpreted whenever it happens to carry a leading `0` followed only by octal digits (`0`–`7`). The chain does not reject the malformed-looking string; it produces a materially different transferred amount without any error surfaced to the caller.

### Impact Explanation
Where this parser is fed a string whose numeric intent does not match its octal reinterpretation, the amount actually debited/credited by the bank module differs — silently and without error — from the amount a caller, indexer, or off-chain accounting system believes was moved. This falls into the "fee or refund abuse" / value-discrepancy category the scan accepts: a contract, escrow, or bridge logic that treats the string amount as ground truth for its own internal accounting (mint/burn ledger, allowance tracking, refund calculation) while the chain settles a smaller (occasionally larger, depending on which value is treated as authoritative) native-token amount, creates an exploitable accounting gap that can be used to under-settle real transfers while over-crediting internal bookkeeping, or vice versa.

The severity is limited by the fact that Rust/CosmWasm's standard `Uint128::from_str`/`Uint128::to_string()` round trip does not produce leading-zero decimal strings, so contracts that validate amounts through the standard CosmWasm numeric types before building coin fields are not affected. The exposure is limited to code paths (custom contract logic, or other Go-side callers of `sdk.NewIntFromString`/`ParseUint`) that pass a raw, unvalidated string straight through to `sdk.NewIntFromString`.

### Likelihood Explanation
Exploitability is Medium, not Critical: the attacker needs a contract (their own, or an existing one that mishandles amount strings) where a leading-zero, all‑octal-digit numeric string can be injected into a `Coin.Amount` field before it reaches `ConvertWasmCoinToSdkCoin`/`ConvertWasmCoinsToSdkCoins`, or must find another Go-side caller of `sdk.NewIntFromString`/`ParseUint` that accepts unvalidated user strings directly. Given that the standard CosmWasm SDK types insulate most "normal" contract code from this, the more likely trigger is a custom/attacker-deployed contract intentionally crafting such strings to desynchronize its own internal ledger from actual bank-module balances.

### Recommendation
Reject or normalize non-decimal-looking numeric strings before calling `big.Int.SetString`. Concretely, in `sei-cosmos/types/int.go` (`newIntegerFromString`) and `sei-cosmos/types/uint.go` (`ParseUint`), switch to `SetString(s, 10)` (strict decimal) for amount/coin parsing paths, or explicitly strip/validate any leading zeros before allowing base-0 auto-detection, so a string like `"0123"` is never silently reinterpreted as octal.

### Proof of Concept
1. In a Go test against `sei-cosmos/types`:
```go
i, ok := sdk.NewIntFromString("0123")
// ok == true, i.String() == "83"  (octal 0123), not the decimal-intended "123"
```
2. Equivalently, calling `ConvertWasmCoinToSdkCoin(wasmvmtypes.Coin{Denom:"usei", Amount:"0123"})` returns a valid `sdk.Coin{Amount: 83usei}` with no error, even though the string visually reads as "123".
3. A CosmWasm contract that accepts a user-supplied numeric string and forwards it verbatim as a `Coin.Amount` in a `BankMsg::Send`/submessage `funds` field (bypassing `Uint128::from_str` validation) will have the sei-chain settle a different, smaller amount than the string appears to represent, with no on-chain error.

### Citations

**File:** sei-cosmos/types/int.go (L13-18)
```go
func newIntegerFromString(s string) (*big.Int, bool) {
	if len(s) > 300 {
		return nil, false
	}
	return new(big.Int).SetString(s, 0)
}
```

**File:** sei-cosmos/types/int.go (L121-133)
```go
// NewIntFromString constructs Int from string
func NewIntFromString(s string) (res Int, ok bool) {
	i, ok := newIntegerFromString(s)
	if !ok {
		return
	}
	// Check overflow
	if i.BitLen() > maxBitLen {
		ok = false
		return
	}
	return Int{i}, true
}
```

**File:** sei-cosmos/types/uint.go (L228-238)
```go
// ParseUint reads a string-encoded Uint value and return a Uint.
func ParseUint(s string) (Uint, error) {
	if len(s) > 300 {
		return Uint{}, fmt.Errorf("unsigned integer string too long: got %d, max 300", len(s))
	}
	i, ok := new(big.Int).SetString(s, 0)
	if !ok {
		return Uint{}, fmt.Errorf("cannot convert %q to big.Int", s)
	}
	return checkNewUint(i)
}
```

**File:** sei-wasmd/x/wasm/keeper/handler_plugin_encoders.go (L303-313)
```go
// ConvertWasmCoinToSdkCoin converts a wasm vm type coin to sdk type coin
func ConvertWasmCoinToSdkCoin(coin wasmvmtypes.Coin) (sdk.Coin, error) {
	amount, ok := sdk.NewIntFromString(coin.Amount)
	if !ok {
		return sdk.Coin{}, sdkerrors.Wrap(sdkerrors.ErrInvalidCoins, coin.Amount+coin.Denom)
	}
	r := sdk.Coin{
		Denom:  coin.Denom,
		Amount: amount,
	}
	return r, r.Validate()
```
