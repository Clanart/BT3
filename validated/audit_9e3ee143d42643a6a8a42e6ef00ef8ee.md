### Title
Silent decode failure ignored via disabled assertion in `Message::mergeFrom()` - (File: `php/ext/google/protobuf/message.c`)

### Summary
The PHP extension's `Message::mergeFrom()` re-serializes the source message and calls `upb_Decode()` into the destination message, but only validates success via `PBPHP_ASSERT(ok)` rather than an unconditional runtime check. `PBPHP_ASSERT` compiles to a no-op unless `PBPHP_ENABLE_ASSERTS` is defined, so in a standard (non-debug) build a decode failure is silently discarded — the call returns to PHP userland as if the merge succeeded, exactly mirroring the reported invariant: "a status-returning operation's failure return value is not checked, so the caller/observer believes an operation succeeded when it actually did not, corrupting downstream integrity."

### Finding Description
`Message::mergeFrom()` re-encodes `from->msg` to bytes and decodes those bytes into `intern->msg`: [1](#0-0) 

The decode status is captured in `ok`, but the only check performed is:
```c
ok = upb_Decode(pb, size, intern->msg, l, NULL, 0, arena) == kUpb_DecodeStatus_Ok;
PBPHP_ASSERT(ok);
``` [2](#0-1) 

`PBPHP_ASSERT` is defined as a real abort-on-failure check only when `PBPHP_ENABLE_ASSERTS` is defined; otherwise it expands to a statement that never evaluates its argument for effect: [3](#0-2) 

Contrast this with the adjacent, correctly-guarded sibling method `Message::mergeFromString()`, which explicitly checks the decode status and throws a PHP exception on failure: [4](#0-3) 

This shows the codebase's own established pattern for handling `upb_Decode` failures (raise an exception) is bypassed in `mergeFrom()`, whose only guard is the assert that compiles away in release/production builds.

This maps to the report's failed invariant: an operation that can fail returns a status/boolean, but the caller does not surface that failure to the trusted consuming application — the call site swallows the failure and lets execution continue as though the "transfer" (here, a merge into the destination message) succeeded.

### Impact Explanation
When `upb_Decode` fails inside `mergeFrom()` (e.g. because the source message, once re-serialized, exceeds `arena`-imposed limits, hits `kUpb_DecodeStatus_MaxDepthExceeded`/`OutOfMemory`/other error paths), the destination message is left in a partially-decoded, indeterminate state, and the PHP-level `mergeFrom()` call returns normally with no exception and no error signal. Application code that relies on `mergeFrom()` either succeeding or throwing (mirroring `mergeFromString()`'s behavior) will silently operate on an incompletely merged/corrupted message, propagating incorrect or missing field data through the application — an integrity failure directly analogous to the Cooler.sol issue where a caller trusted a return value that was never actually validated.

### Likelihood Explanation
`mergeFrom()` is a bounded, public parsing-adjacent API taking an ordinary same-class PHP message; a client only needs to construct a `from` message whose re-encoded bytes trigger a `upb_Decode` failure mode (max depth, malformed extension registry interaction, etc.) relative to the target arena/mini-table constraints. Because `PBPHP_ENABLE_ASSERTS` is not defined in standard release builds of the PHP extension, this is not a debug-only edge case — it is the default production behavior. The trigger requires no privileged access, no hostile schema, and no unbounded resource growth — only a same-descriptor message pair whose merge decode legitimately fails.

### Recommendation
Replace the `PBPHP_ASSERT(ok)` in `Message::mergeFrom()` with an explicit, unconditional check that surfaces failure to PHP userland, consistent with `mergeFromString()`'s handling:
```c
upb_EncodeStatus estatus = upb_Encode(from->msg, l, 0, arena, &pb, &size);
if (!Message_checkEncodeStatus(estatus)) return;

if (upb_Decode(pb, size, intern->msg, l, NULL, 0, arena) != kUpb_DecodeStatus_Ok) {
  zend_throw_exception_ex(NULL, 0, "Error occurred during merge");
  return;
}
```
This removes reliance on an assertion macro that is compiled out in production and ensures decode failures are never silently discarded.

### Proof of Concept
Static code inspection confirms the reachable bypass:
1. `php/ext/google/protobuf/protobuf.h:61-75` — `PBPHP_ASSERT` is a no-op unless `PBPHP_ENABLE_ASSERTS` is defined (not defined by default release build configuration for the extension).
2. `php/ext/google/protobuf/message.c:681-687` — `Message::mergeFrom()`'s only failure check on `upb_Decode`'s return status is this compiled-out assert.
3. `php/ext/google/protobuf/message.c:695-723` — the sibling `mergeFromString()` demonstrates the correct pattern (explicit `!=` check that throws `zend_throw_exception_ex`), confirming that `mergeFrom()`'s omission is a deviation from the codebase's own established failure-handling convention rather than an intentional design choice.

I was not able to execute this against a live build to observe the runtime PHP-visible symptom (e.g., confirming the exact `upb_Decode` failure mode reachable purely from a same-descriptor source message without additional privileged inputs), since I only have static/index-based access to the repository. This should be verified with a running PHP extension build (default flags, `PBPHP_ENABLE_ASSERTS` undefined) by constructing a source message that induces a `upb_Decode` failure during merge (e.g. via crafted lazy/oneof/extension state under a shared arena) and confirming the destination message and script continue executing without any exception or error being raised.

### Citations

**File:** php/ext/google/protobuf/message.c (L659-687)
```c
PHP_METHOD(Message, mergeFrom) {
  Message* intern = (Message*)Z_OBJ_P(getThis());
  Message* from;
  upb_Arena* arena = Arena_Get(&intern->arena);
  const upb_MiniTable* l = upb_MessageDef_MiniTable(intern->desc->msgdef);
  zval* value;
  char* pb;
  size_t size;
  bool ok;

  if (zend_parse_parameters(ZEND_NUM_ARGS(), "O", &value,
                            intern->desc->class_entry) == FAILURE) {
    return;
  }

  from = (Message*)Z_OBJ_P(value);

  // Should be guaranteed since we passed the class type to
  // zend_parse_parameters().
  PBPHP_ASSERT(from->desc == intern->desc);

  // TODO: use a temp arena for this.
  upb_EncodeStatus status = upb_Encode(from->msg, l, 0, arena, &pb, &size);
  if (!Message_checkEncodeStatus(status)) return;

  ok = upb_Decode(pb, size, intern->msg, l, NULL, 0, arena) ==
       kUpb_DecodeStatus_Ok;
  PBPHP_ASSERT(ok);
}
```

**File:** php/ext/google/protobuf/message.c (L695-723)
```c
PHP_METHOD(Message, mergeFromString) {
  Message* intern = (Message*)Z_OBJ_P(getThis());
  char* data = NULL;
  zend_long data_len;
  zend_long recursion_limit = 0; /* 0 = use library default */
  const upb_MiniTable* l = upb_MessageDef_MiniTable(intern->desc->msgdef);
  upb_Arena* arena = Arena_Get(&intern->arena);

  if (zend_parse_parameters(ZEND_NUM_ARGS(), "s|l", &data, &data_len,
                            &recursion_limit) == FAILURE) {
    return;
  }

  int options = 0;
  if (recursion_limit != 0) {
    if (recursion_limit < 1 || recursion_limit > 0xffff) {
      zend_throw_exception_ex(NULL, 0,
                              "recursion_limit must be between 1 and 65535");
      return;
    }
    options = upb_DecodeOptions_MaxDepth((uint16_t)recursion_limit);
  }

  if (upb_Decode(data, data_len, intern->msg, l, NULL, options, arena) !=
      kUpb_DecodeStatus_Ok) {
    zend_throw_exception_ex(NULL, 0, "Error occurred during parsing");
    return;
  }
}
```

**File:** php/ext/google/protobuf/protobuf.h (L61-75)
```text
// We need our own assert() because PHP takes control of NDEBUG in its headers.
#ifdef PBPHP_ENABLE_ASSERTS
#define PBPHP_ASSERT(x)                                                    \
  do {                                                                     \
    if (!(x)) {                                                            \
      fprintf(stderr, "Assertion failure at %s:%d %s", __FILE__, __LINE__, \
              #x);                                                         \
      abort();                                                             \
    }                                                                      \
  } while (false)
#else
#define PBPHP_ASSERT(x) \
  do {                  \
  } while (false && (x))
#endif
```
