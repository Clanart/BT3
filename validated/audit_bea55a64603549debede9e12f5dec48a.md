### Title
Unchecked `upb_Decode` Status in PHP `Message::mergeFrom()` Silently Accepts Partial/Corrupted Merge - (File: php/ext/google/protobuf/message.c)

### Summary
`Message::mergeFrom()` in the PHP upb-based extension serializes the "from" message with `upb_Encode()` and re-decodes the resulting bytes directly into the destination message's live `upb_Message*` via `upb_Decode()`. The decode status is only checked with `PBPHP_ASSERT(ok)`, a macro that compiles to a no-op unless `PBPHP_ENABLE_ASSERTS` is defined. In default/production PHP extension builds, a failed `upb_Decode()` is silently ignored: the function returns normally with `void` result, leaving `intern->msg` in a partially-merged, internally inconsistent state, exactly analogous to the reported unchecked-`transferFrom`-return-value bug where a failed operation is treated as successful and execution continues to operate on stale/incorrect state.

### Finding Description
```c
// php/ext/google/protobuf/message.c
upb_EncodeStatus status = upb_Encode(from->msg, l, 0, arena, &pb, &size);
if (!Message_checkEncodeStatus(status)) return;

ok = upb_Decode(pb, size, intern->msg, l, NULL, 0, arena) ==
     kUpb_DecodeStatus_Ok;
PBPHP_ASSERT(ok);
``` [1](#0-0) 

The failed-operation invariant transfers directly: the encode path (`upb_Encode`) *is* checked via `Message_checkEncodeStatus`, but the decode path — the operation that actually mutates the live destination message `intern->msg` — is only guarded by `PBPHP_ASSERT`, defined as:
```c
#ifdef PBPHP_ENABLE_ASSERTS
#define PBPHP_ASSERT(x) ... abort() ...
#else
#define PBPHP_ASSERT(x) \
  do {                  \
  } while (false && (x))
#endif
``` [2](#0-1) 

`PBPHP_ENABLE_ASSERTS` is not defined by any normal build path — it appears only referenced in the test compilation script, not in the production extension build config [3](#0-2) . This means in the shipped PHP extension, a non-OK `upb_Decode` return is completely swallowed: the assert macro degenerates to a discarded statement, `ok` is never inspected, and `Message_mergeFrom` returns to PHP userland as if the merge succeeded, without raising a `TypeError`, `Exception`, or any diagnosable error.

This mirrors the ERC20 report's core failure mode: a call whose return/status value signals failure (`transferFrom` returning `false`; `upb_Decode` returning non-`kUpb_DecodeStatus_Ok`) is not checked, and the caller proceeds to use/report on data as if the operation succeeded, silently leaving stale or inconsistent state (unspent tokens counted as deposited; partially-decoded fields merged as if fully merged).

### Impact Explanation
When `upb_Decode` fails partway through (e.g., hitting the decoder recursion/depth limit, a malformed length-prefix produced by an unusual `from` message, or an allocation failure inside the arena), `intern->msg` can end up holding a partially populated merge: some fields from `from` applied, others not, with no signal to the calling PHP code. Application logic that relies on `mergeFrom()` succeeding (e.g., merging incoming untrusted-but-schema-valid client messages into a canonical state object, then acting on invariants across combined fields) can silently operate on an inconsistent object graph — a data-integrity defect reachable purely through the public PHP `Message` API, without needing memory corruption for it to matter to the calling application.

### Likelihood Explanation
Reachability requires only calling the public, documented `Message::mergeFrom()` PHP API with a same-schema message — no privileged access needed. Triggering an actual `upb_Decode` failure during this internal encode/decode roundtrip requires constructing a `from` message that trips a decoder-side invariant not enforced during encode (most plausibly the recursion/depth limit via deeply nested submessages built up through ordinary PHP message construction, since `upb_Encode` and `upb_Decode` do not necessarily share identical limits for a given message shape). This is a bounded, attacker-reachable path from ordinary client-controlled data through a supported API, but the failure trigger is narrower/harder to reliably hit than an always-observable path, so likelihood is Medium.

### Recommendation
Check the `upb_Decode` status returned in `Message_mergeFrom` (mirroring the existing `Message_checkEncodeStatus` check on the encode path) and raise a PHP exception (or otherwise fail loudly) on any non-`kUpb_DecodeStatus_Ok` result instead of relying on `PBPHP_ASSERT`, which is compiled out in production builds:
```c
upb_DecodeStatus decode_status =
    upb_Decode(pb, size, intern->msg, l, NULL, 0, arena);
if (decode_status != kUpb_DecodeStatus_Ok) {
  zend_throw_exception_ex(NULL, 0, "Error merging message: decode failed");
  return;
}
```

### Proof of Concept
Exact reproduction requires driving `upb_Decode`'s internal depth/recursion limit differently from `upb_Encode`'s limit for a message constructed purely through the public PHP API and then calling:
```php
$dst = new SameSchemaMessage();
$dst->mergeFrom($deeplyNestedFromMessage); // triggers internal upb_Encode -> upb_Decode roundtrip
```
With the current code, if the internal `upb_Decode(pb, size, intern->msg, l, NULL, 0, arena)` call returns a non-OK status (verifiable by instrumenting a debug build with `PBPHP_ENABLE_ASSERTS` defined, which aborts on this exact assertion, confirming the failure path is reachable), the production (non-asserting) build proceeds silently, and `mergeFrom()` returns without error to the PHP caller despite the underlying merge being incomplete. I was not able to fully verify the precise depth threshold or a minimal concrete nesting depth needed to flip `upb_Decode` to failure while `upb_Encode` still succeeds within this indexed checkout — confirming the exact numeric trigger would require running the compiled extension, which is outside available tools here.

### Citations

**File:** php/ext/google/protobuf/message.c (L674-688)
```c
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

**File:** php/tests/compile_extension.sh (L1-1)
```shellscript
#!/bin/bash
```
