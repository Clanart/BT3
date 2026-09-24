## Title
Undefined return value from unhandled-exception fallthrough in `GPBUnknownFields` parsing silently corrupts/truncates unknown-field state - (File: `objectivec/GPBUnknownFields.m`)

## Summary
The `_CreateAuction` bug's essential failure pattern is: a `try/catch` block intended to guard against a narrow class of expected failures instead catches an unanticipated failure mode (an out-of-gas revert), and the code inside the `catch` branch performs an action whose consequence (persisted pause state, returned as success) misrepresents the true outcome of the operation to the rest of the system. The Protobuf analog is `MergeFromInputStream` in `objectivec/GPBUnknownFields.m` (lines 46-128): the function is declared to return `BOOL`, but its `@catch (NSException *exception)` block (lines 122-127) has no `return` statement. When an attacker-controlled, bounded parse triggers the exception path, the function falls off its end, and the caller receives an **undefined/indeterminate `BOOL` value** instead of an explicit success/failure signal — exactly the same class of defect: an overly-broad catch clause papering over an unexpected failure and letting a corrupted/incomplete result silently masquerade as a valid one to the calling code.

## Finding Description
`GPBMessage` stores unrecognized fields as raw bytes and lazily materializes them into a `GPBUnknownFields` object via the public accessor `-[GPBMessage unknownFields]` (backed by `-[GPBUnknownFields initFromMessage:]`, `objectivec/GPBUnknownFields.m:132-156`). That initializer calls the static helper `MergeFromInputStream(self, input, 0)` and gates success purely on its `BOOL` return value:

```objc
if (!MergeFromInputStream(self, input, 0)) {
    ...
    [NSException raise:NSInternalInconsistencyException
                format:@"Internal error: Unknown field data from message was malformed."];
}
``` [1](#0-0) 

`MergeFromInputStream` itself wraps its entire tag-parsing loop in `@try`, and the `@catch` clause is a no-op in release builds (only logs under `DEBUG`) and — critically — never executes a `return` statement:

```objc
} @catch (NSException *exception) {
#if defined(DEBUG) && DEBUG
    NSLog(@"%@: Internal exception while parsing unknown data, this shouldn't happen!: %@",
          [self class], exception);
#endif
}
``` [2](#0-1) 

This is a function declared `static BOOL MergeFromInputStream(...)` [3](#0-2)  — falling off the end without a return statement is undefined behavior in C/Objective-C; the caller receives whatever garbage value happens to be in the return register/stack slot.

The exception path is trivially reachable with bounded, attacker-controlled bytes: a raw wire tag whose wire type is `GPBWireFormatEndGroup` at the top level (where `endTag == 0`) falls through the `tag == endTag` and `tag == 0` checks and lands in the `case GPBWireFormatEndGroup:` branch, which unconditionally raises `NSInternalInconsistencyException`:

```objc
case GPBWireFormatEndGroup:
    [NSException raise:NSInternalInconsistencyException
                format:@"Unexpected end group tag: %u", tag];
    break;
``` [4](#0-3) 

Nested/mismatched group tags reach the same outcome via the recursive-group branch as well: [5](#0-4) 

**Invariant that fails to transfer safely:** the caller's contract assumes a `NO` return unambiguously signals "parsing failed, this data was malformed" and a truthy return unambiguously signals "all bytes before the tag stream were fully and correctly consumed into `fields_`." The broad `@catch` block breaks this invariant exactly like the Solidity `catch {}` broke the assumption that only the intended `mint()` errors would be caught — in both cases, an unanticipated exceptional path is silently absorbed and the surrounding control flow proceeds based on a value that does not reflect the true state of the operation.

## Impact Explanation
If the undefined return happens to evaluate truthy, `initFromMessage:` treats parsing as successful and returns a `GPBUnknownFields` object whose `fields_` array is **silently incomplete/truncated** (some previously-added entries are retained, but the malformed tag and everything after it are dropped without any indication to the caller). Any code path built on `-[GPBMessage unknownFields]` (e.g. round-tripping unknown fields, proxying, forwarding of unrecognized fields in a gateway/multiplexer) will silently and non-deterministically lose data on attacker-controlled bounded input, an integrity failure reachable purely through a supported public API (`-unknownFields` / `-mergeFromData:...`) with no privileged access, matching the "impacting core protocol functionality without direct fund/memory-growth loss" classification that the Sherlock panel ultimately settled on as valid Medium severity for the auction issue. If the value happens to be falsy, the caller instead raises an assertion (`NSInternalInconsistencyException`) that, depending on caller error handling, can itself become an availability problem for that call.

## Likelihood Explanation
The triggering condition requires only a handful of attacker-chosen bytes (a tag encoding wire type 4 as an "unknown" field, or a mismatched nested end-group tag) inside an otherwise valid, schema-conforming message — no privileged access, no huge payloads, no schema control, and no resource-exhaustion assumptions are needed. The only precondition is that the consuming application calls `-unknownFields` (or otherwise triggers `GPBUnknownFields` materialization) on attacker-supplied messages, which is a common and supported usage pattern (e.g. proxies/gateways that must preserve unknown fields). This is a deterministic, reproducible defect (missing `return`), not merely a low-probability racing/gas condition, making it at least as reachable as the original finding.

## Recommendation
Add explicit `return NO;` (and any necessary cleanup of partially-populated `fields_`) inside the `@catch` block of `MergeFromInputStream` so that any exception during parsing is guaranteed to be surfaced as a definite parse failure, rather than falling through to an undefined return value:
```objc
} @catch (NSException *exception) {
#if defined(DEBUG) && DEBUG
    NSLog(@"%@: Internal exception while parsing unknown data, this shouldn't happen!: %@",
          [self class], exception);
#endif
    return NO;
}
```
Additionally, consider clearing/rolling back `fields_` entries added during the failed parse attempt so a caller who does receive `NO` doesn't need to worry about partially-mutated shared state, and add a compiler `-Wreturn-type`/static-analysis check (e.g. build-time warning-as-error) to prevent this class of fallthrough in exception handlers from recurring elsewhere in the Objective-C runtime.

## Proof of Concept
1. Construct a serialized message for any generated `GPBMessage` subclass that includes one unrecognized field number encoded with wire type 4 (`EndGroup`), e.g. raw bytes `0x?C` where the low 3 bits equal `100` (binary) for an arbitrary unused field number — this is stored by the general parser as unknown-field data (`objectivec/GPBMessage.m` `ParseUnknownField`/unknown field capture path).
2. Parse this message normally via the public API: `GPBMessage *msg = [MyMessage parseFromData:data error:&err];` — this succeeds since generic unknown-field capture at the message level does not itself walk into `GPBUnknownFields`.
3. Call `GPBUnknownFields *uf = [[GPBUnknownFields alloc] initFromMessage:msg];` (or the equivalent public accessor that lazily builds this view).
4. Internally, this invokes `MergeFromInputStream(uf, input, 0)`, which reads the crafted tag, falls into `case GPBWireFormatEndGroup:`, raises `NSInternalInconsistencyException`, which is caught by the un-returning `@catch` block at `objectivec/GPBUnknownFields.m:122-127`.
5. Observe (by instrumenting/building with `-Wreturn-type` promoted to error, or by inspecting disassembly/register state on the target ABI) that the function returns a value that is not deterministically tied to the actual outcome — demonstrating the undefined-behavior fallthrough. Compilers such as Clang will flag this with `-Wreturn-type` ("control reaches end of non-void function") when that warning is enabled, confirming the defect independent of the exact garbage value observed at runtime.

### Citations

**File:** objectivec/GPBUnknownFields.m (L45-47)
```text
GPB_NOINLINE
static BOOL MergeFromInputStream(GPBUnknownFields *self, GPBCodedInputStream *input,
                                 uint32_t endTag) {
```

**File:** objectivec/GPBUnknownFields.m (L100-114)
```text
        case GPBWireFormatStartGroup: {
          GPBUnknownFields *group = [[GPBUnknownFields alloc] init];
          GPBUnknownField *field = [[GPBUnknownField alloc] initWithNumber:fieldNumber group:group];
          [fields addObject:field];
          [field release];
          [group release];  // Still will be held in the field/fields.
          uint32_t endGroupTag = GPBWireFormatMakeTag((uint32_t)fieldNumber, GPBWireFormatEndGroup);
          if (MergeFromInputStream(group, input, endGroupTag)) {
            GPBCodedInputStreamCheckLastTagWas(state, (int32_t)endGroupTag);
          } else {
            [NSException
                 raise:NSInternalInconsistencyException
                format:@"Internal error: Unknown field data for nested group was malformed."];
          }
          break;
```

**File:** objectivec/GPBUnknownFields.m (L116-119)
```text
        case GPBWireFormatEndGroup:
          [NSException raise:NSInternalInconsistencyException
                      format:@"Unexpected end group tag: %u", tag];
          break;
```

**File:** objectivec/GPBUnknownFields.m (L122-128)
```text
  } @catch (NSException *exception) {
#if defined(DEBUG) && DEBUG
    NSLog(@"%@: Internal exception while parsing unknown data, this shouldn't happen!: %@",
          [self class], exception);
#endif
  }
}
```

**File:** objectivec/GPBUnknownFields.m (L142-151)
```text
    NSData *data = GPBMessageUnknownFieldsData(message);
    if (data) {
      GPBCodedInputStream *input = [[GPBCodedInputStream alloc] initWithData:data];
      // Parse until the end of the data (tag will be zero).
      if (!MergeFromInputStream(self, input, 0)) {
        [input release];
        [self release];
        [NSException raise:NSInternalInconsistencyException
                    format:@"Internal error: Unknown field data from message was malformed."];
      }
```
