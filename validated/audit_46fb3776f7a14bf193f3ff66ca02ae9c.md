### Title
Unbounded Recursive JSON Traversal in `recursivelyAddTypesToRoot` Enables O(n²) Resource Exhaustion via Crafted EIP-712 Sign Doc — (File: `ethereum/eip712/types.go`)

---

### Summary

The `recursivelyAddTypesToRoot` function in `ethereum/eip712/types.go` processes JSON objects recursively to build EIP-712 type definitions with no depth limit. An unprivileged attacker can submit an EIP-712-signed Cosmos SDK transaction whose message `value` field contains a deeply nested JSON object. Because the `prefix` string grows linearly with depth and `sanitizeTypedef` processes it on every recursive call, the total CPU work is O(d²) and the total typeMap memory is O(d²) in nesting depth `d`. With a standard 1 MB transaction size limit, an attacker can embed ≈174,000 nesting levels, triggering tens of gigabytes of allocation and billions of CPU operations during ante-handler signature verification. The existing `doRecover` panic handler cannot intercept a Go OOM fatal error, so the validator process crashes.

---

### Finding Description

**Root cause — no depth guard in `recursivelyAddTypesToRoot`**

`recursivelyAddTypesToRoot` (lines 158–235 of `ethereum/eip712/types.go`) iterates over every field of a `gjson.Result` object. When a field is itself an object, it calls itself with an extended `prefix`:

```go
// ethereum/eip712/types.go  lines 215-231
if field.IsObject() {
    fieldPrefix := prefixForSubField(prefix, fieldName)          // prefix grows by len(fieldName)+1
    fieldTypeDef, err := recursivelyAddTypesToRoot(typeMap, rootType, fieldPrefix, field)
    ...
    fieldTypeDef = sanitizeTypedef(fieldTypeDef)                 // O(|fieldTypeDef|) work
    ...
}
```

There is no `depth` parameter, no counter, and no early-exit guard. The only existing limit is `maxDuplicateTypeDefs = 1000` inside `addTypesToRoot`, which bounds duplicate type-name collisions, not recursion depth. [1](#0-0) 

**

### Citations

**File:** ethereum/eip712/types.go (L158-235)
```go
func recursivelyAddTypesToRoot(
	typeMap apitypes.Types,
	rootType string,
	prefix string,
	payload gjson.Result,
) (string, error) {
	typesToAdd := []apitypes.Type{}

	// Must sort the JSON keys for deterministic type generation.
	sortedFieldNames, err := sortedJSONKeys(payload)
	if err != nil {
		return "", errorsmod.Wrap(err, "unable to sort object keys")
	}

	typeDef := typeDefForPrefix(prefix, rootType)

	for _, fieldName := range sortedFieldNames {
		field := payload.Get(fieldName)
		if !field.Exists() {
			continue
		}

		// Handle array type by unwrapping the first element.
		// Note that arrays with multiple types are not supported
		// using EIP-712, so we can ignore that case.
		isCollection := false
		if field.IsArray() {
			fieldAsArray := field.Array()

			if len(fieldAsArray) == 0 {
				// Arbitrarily add string[] type to handle empty arrays,
				// since we cannot access the underlying object.
				emptyArrayType := "string[]"
				typesToAdd = appendedTypesList(typesToAdd, fieldName, emptyArrayType)

				continue
			}

			field = fieldAsArray[0]
			isCollection = true
		}

		ethType := getEthTypeForJSON(field)

		// Handle JSON primitive types by adding the corresponding
		// EIP-712 type to the types schema.
		if ethType != "" {
			if isCollection {
				ethType += "[]"
			}
			typesToAdd = appendedTypesList(typesToAdd, fieldName, ethType)

			continue
		}

		// Handle object types recursively. Note that nested array types are not supported
		// in EIP-712, so we can exclude that case.
		if field.IsObject() {
			fieldPrefix := prefixForSubField(prefix, fieldName)

			fieldTypeDef, err := recursivelyAddTypesToRoot(typeMap, rootType, fieldPrefix, field)
			if err != nil {
				return "", err
			}

			fieldTypeDef = sanitizeTypedef(fieldTypeDef)
			if isCollection {
				fieldTypeDef += "[]"
			}

			typesToAdd = appendedTypesList(typesToAdd, fieldName, fieldTypeDef)

			continue
		}
	}

	return addTypesToRoot(typeMap, typeDef, typesToAdd)
}
```
