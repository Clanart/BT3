### Title
Nil-Pointer Dereference via Null Element in `AccountKeyRoleBased` JSON Array Crashes Kaia Node - (File: `blockchain/types/accountkey/account_key_role_based.go`)

### Summary
An authenticated RPC caller (any account holder able to submit an `AccountUpdate` transaction through the standard `klay_sendTransaction` / `klay_signTransaction` JSON-RPC interface) can crash the Kaia node by submitting a role-based `AccountKey` JSON payload that contains a literal `null` entry inside the role-key array. `AccountKeyRoleBased.UnmarshalJSON` dereferences each decoded element without checking for `nil`, exactly mirroring the Incus `CreateCustomVolumeFromBackup` bug class (CWE-476: unmarshaled slice of pointers dereferenced without a nil check).

### Finding Description
`AccountKeyRoleBased` is defined as a slice of `AccountKey` interfaces and is JSON-decoded like this: [1](#0-0) 

`json.Unmarshal(b, &serializers)` decodes into `[]*AccountKeySerializer`. Per Go's `encoding/json` semantics, when an array element in the input is the JSON literal `null`, the corresponding slice element is left as a `nil` pointer — the custom `AccountKeySerializer.UnmarshalJSON` method (which normally initializes the `key` field) is never invoked for a `null` element: [2](#0-1) 

Back in `AccountKeyRoleBased.UnmarshalJSON`, the loop `(*a)[i] = s.key` unconditionally accesses the `key` field of `s`, which is a nil `*AccountKeySerializer` for the crafted null entry. Dereferencing a nil pointer's field triggers a Go runtime `nil pointer dereference` panic, which is unrecoverable in that call path and crashes the process handling the RPC request (`net/http`/RPC goroutines do not generally protect handler code called deep inside transaction/account-key decoding from panics propagating and terminating the daemon, depending on recovery middleware coverage).

The `AccountKey` interface (of which `AccountKeyRoleBased` is one concrete implementation) is the field type used by the account-update transaction: [3](#0-2) 

and `AccountKeySerializer`/`AccountKeyRoleBased` are the types used to carry attacker-controlled account key JSON through the JSON-RPC entry points (e.g. `AccountKey *accountkey.AccountKeySerializer` fields referenced in `api/api_kaia.go` and `api/api_kaia_blockchain.go`), i.e. exactly the kind of "AccountKey authorization" input reachable from a single submitted RPC call, as called out in scope.

### Impact Explanation
Successful exploitation crashes the Kaia node process handling the RPC/transaction submission. Because any account holder capable of forming an `AccountUpdate` transaction (or calling the relevant RPC method that accepts an `AccountKeySerializer`/`AccountKeyRoleBased` JSON payload) can trigger this without special privileges, it can be repeated to keep a node offline — a Denial-of-Service condition with CVSS Availability impact analogous to the referenced advisory (`A:H`, `C:N`, `I:N`). No unauthorized value movement or state divergence occurs, but node availability is compromised.

### Likelihood Explanation
The attack requires only a normal RPC-connected account able to submit an `AccountUpdate`-style request; no consensus, peer, or admin privileges are required. Constructing the malicious payload is trivial — a JSON array with a `null` element inside `AccountKeyRoleBased`'s serialized key list. This makes the likelihood high for any node that exposes this RPC surface to external callers.

### Recommendation
In `AccountKeyRoleBased.UnmarshalJSON` (and correspondingly `AccountKeyRoleBased.DecodeRLP`/RLP path if similarly vulnerable), validate that each decoded `*AccountKeySerializer` element is non-nil before dereferencing `s.key`. Return a structured validation error (e.g., `errors.New("nil account key in role-based key list")`) instead of allowing the panic to propagate. The same defensive check should be applied anywhere else attacker-controlled slices of pointers (RLP or JSON) are dereferenced without a preceding nil check.

### Proof of Concept
Submit an `AccountUpdate`-type transaction (or otherwise call the RPC path that decodes an `AccountKeySerializer`) whose `key` field, for `keyType` = RoleBased, is a JSON array containing a `null` entry, e.g.:
```json
{
  "keyType": 5,
  "key": [
    null,
    {"keyType": 2, "key": {"x": "0x...", "y": "0x..."}},
    {"keyType": 2, "key": {"x": "0x...", "y": "0x..."}}
  ]
}
```
When this reaches `AccountKeyRoleBased.UnmarshalJSON` [1](#0-0) , `json.Unmarshal` decodes the first array element as a nil `*AccountKeySerializer`, and the subsequent `(*a)[i] = s.key` dereferences it, panicking with `nil pointer dereference` and crashing the node's RPC-handling goroutine/process.

### Citations

**File:** blockchain/types/accountkey/account_key_role_based.go (L150-162)
```go
func (a *AccountKeyRoleBased) UnmarshalJSON(b []byte) error {
	var serializers []*AccountKeySerializer
	if err := json.Unmarshal(b, &serializers); err != nil {
		return err
	}

	*a = make(AccountKeyRoleBased, len(serializers))
	for i, s := range serializers {
		(*a)[i] = s.key
	}

	return nil
}
```

**File:** blockchain/types/accountkey/account_key_serializer.go (L84-103)
```go
func (serializer *AccountKeySerializer) UnmarshalJSON(b []byte) error {
	var keyJSON AccountKeyJSON

	if err := json.Unmarshal(b, &keyJSON); err != nil {
		return err
	}

	if keyJSON.KeyType == nil {
		return errNoKeyType
	}
	serializer.keyType = *keyJSON.KeyType

	var err error
	serializer.key, err = NewAccountKey(serializer.keyType)
	if err != nil {
		return err
	}

	return json.Unmarshal(keyJSON.Key, serializer.key)
}
```

**File:** blockchain/types/tx_internal_data_account_update.go (L33-39)
```go
// TxInternalDataAccountUpdate represents a transaction updating a key of an account.
type TxInternalDataAccountUpdate struct {
	AccountNonce uint64
	Price        *big.Int
	GasLimit     uint64
	From         common.Address
	Key          accountkey.AccountKey
```
