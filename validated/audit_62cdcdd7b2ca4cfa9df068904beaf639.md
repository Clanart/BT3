[1](#0-0) [2](#0-1)

### Citations

**File:** precompiles/ics20/types.go (L60-63)
```go
// PageRequest defines the data for the page request.
type PageRequest struct {
	PageRequest query.PageRequest
}
```

**File:** precompiles/ics20/types.go (L233-243)
```go
// safeCopyInputs is a helper function to safely copy inputs from the method to the args.
// It recovers from any panic that might occur during the copy operation and returns an error instead.
func safeCopyInputs(method *abi.Method, args []interface{}, pageRequest *PageRequest) (err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("panic during method.Inputs.Copy: %v", r)
		}
	}()
	err = method.Inputs.Copy(pageRequest, args)
	return
}
```
