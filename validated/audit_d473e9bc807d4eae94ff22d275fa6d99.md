### Title
Arena Leak in ProtoJSON Encoding of `Any` Fields on Error Path - (File: upb/json/encode.c)

### Summary
`upb_JsonEncode()` lazily allocates a `upb_Arena` (via `jsonenc_arena()`) the first time it needs to decode an embedded `google.protobuf.Any` message for JSON serialization. If any error occurs afterward — including errors that occur specifically because the arena was needed (bad type URL resolution, `Any` payload that fails to decode) — the encoder calls `jsonenc_err()`/`jsonenc_errf()`, which perform a `longjmp` straight back to the `UPB_SETJMP` point in `upb_JsonEncoder_Encode()`. That jump bypasses the line `if (e->arena) upb_Arena_Free(e->arena);`, so the arena and everything allocated in it are never freed. This mirrors the CVE-2017-11538 pattern: an ordinary error/exit path in a "write"/serialize routine skips a required cleanup call, leaking memory that was allocated to service the current, single request.

### Finding Description
`jsonenc_arena()` allocates `e->arena` on demand: [1](#0-0) 

The top-level encode loop only frees that arena on the *success* path, after `jsonenc_msgfield()` returns normally: [2](#0-1) 

Errors are raised via `jsonenc_err`/`jsonenc_errf`, which never touch `e->arena` and instead `longjmp` directly to the `UPB_SETJMP` check, which unconditionally `return -1`, skipping the arena-free line entirely: [3](#0-2) 

The `Any` handling path is exactly where this arena is created and where several attacker-reachable error conditions exist (unresolved type, decode failure of the packed submessage): [4](#0-3) 

This is the same file/function shape shown in `php-upb.c`'s bundled copy of the encoder, confirming the bug is present in the language-binding-embedded upb copies (PHP, and by extension any upb-based binding using the same encoder), not just the standalone `upb/` sources: [5](#0-4) [6](#0-5) 

The consuming-application exposure assumption: any application that accepts a trusted-schema Protobuf message containing a `google.protobuf.Any` field (or a `Struct`/`Value`/wrapper that routes through the same `jsonenc_any` machinery) from a client, and calls the public ProtoJSON encode API (`Message::serializeToJsonString()` in PHP, `MessageToJson` equivalents in Python/Ruby upb bindings, or `upb_JsonEncode()` directly) is exposed. The attacker only needs to control the *contents* of the `Any` field (e.g., set `type_url` to a value that cannot be resolved in the symbol table, or pack bytes that fail to decode against the resolved type) — both of which are ordinary data values within a bounded, valid message, not malformed wire input.

### Impact Explanation
Each failed JSON encode of a message containing a bad `Any` payload leaks one `upb_Arena` (a heap block, or chain of blocks if the `Any` submessage itself grows during the failed decode) that is never reclaimed. This matches CVE-2017-11538's `C:N/I:N/A:H` profile — no confidentiality/integrity effect, but the missing cleanup is invoked repeatedly by attacker-controlled parameters within a long-running server process (e.g., a JSON gateway that re-serializes proto messages per request), producing a persistent, cumulative memory leak. This is a Medium-severity finding, consistent with the referenced CVE class (leaked allocation on an identifiable error path in a serialization routine), not a flooding/resource-exhaustion primitive — the leak is a fixed, bounded amount of memory per triggering call, directly analogous to `WriteOnePNGImage()` leaking on its error branch.

### Likelihood Explanation
High likelihood of reachability: any application using upb's ProtoJSON encoder (PHP `serializeToJsonString()`, Python/Ruby upb-backed `MessageToJson`) to serialize attacker-influenced data that includes a proto `Any` field with a client-controllable `type_url` or embedded payload will hit this path whenever the `Any` cannot be resolved/decoded — a condition fully controllable by a remote client supplying otherwise valid, bounded Protobuf/ProtoJSON input.

### Recommendation
In `upb/json/encode.c` (and the embedded copy in `php-upb.c`), ensure `e->arena` is freed on every exit path, not just the success path. The simplest fix is to free the arena immediately inside `jsonenc_err`/`jsonenc_errf` before the `longjmp`, or wrap the encode call with a `absl::Cleanup`-style guard (as already used elsewhere in the codebase, e.g. `upb/reflection/internal/def_builder_test.cc`'s `absl::MakeCleanup([arena]{ upb_Arena_Free(arena); })` pattern) so the arena is freed regardless of whether `jsonenc_msgfield` returns normally or via `longjmp`.

### Proof of Concept
1. Define a schema with a message field of type `google.protobuf.Any`.
2. Construct a valid, bounded message where the `Any` field's `type_url` points to a type name that is not registered in the `DefPool`/symbol table used for serialization (or where the packed bytes fail `upb_Decode` against the resolved type).
3. Call the public serialize API, e.g. PHP `Message::serializeToJsonString()` (`php/ext/google/protobuf/message.c:816-866`, which calls `upb_JsonEncode`) or the raw `upb_JsonEncode()` API.
4. Observe: `jsonenc_arena()` allocates `e->arena` while attempting to decode the `Any` payload; `jsonenc_getanymsg`/`jsonenc_any` hits an error path (`jsonenc_err("Couldn't find Any type: ...")` or `jsonenc_err("Error decoding message in Any")`), which `longjmp`s directly to `UPB_SETJMP(e->err)` in `upb_JsonEncoder_Encode`, returning `-1` without ever reaching `if (e->arena) upb_Arena_Free(e->arena);`.
5. Repeating this call in a loop under a leak checker (e.g., valgrind/ASan leak detector) shows monotonically growing live allocations attributable to `upb_Arena_New()` inside `jsonenc_arena`, confirming the leak — the same class of finding as `WriteOnePNGImage()`'s leaked buffer on its error branch.

### Citations

**File:** upb/json/encode.c (L46-70)
```c
static void jsonenc_msg(jsonenc* e, const upb_Message* msg,
                        const upb_MessageDef* m);
static void jsonenc_scalar(jsonenc* e, upb_MessageValue val,
                           const upb_FieldDef* f);
static void jsonenc_msgfield(jsonenc* e, const upb_Message* msg,
                             const upb_MessageDef* m);
static void jsonenc_msgfields(jsonenc* e, const upb_Message* msg,
                              const upb_MessageDef* m, bool first);
static void jsonenc_value(jsonenc* e, const upb_Message* msg,
                          const upb_MessageDef* m);
static void jsonenc_string(jsonenc* e, upb_StringView str);

UPB_NORETURN static void jsonenc_err(jsonenc* e, const char* msg) {
  upb_Status_SetErrorMessage(e->status, msg);
  UPB_LONGJMP(e->err, 1);
}

UPB_PRINTF(2, 3)
UPB_NORETURN static void jsonenc_errf(jsonenc* e, const char* fmt, ...) {
  va_list argp;
  va_start(argp, fmt);
  upb_Status_VSetErrorFormat(e->status, fmt, argp);
  va_end(argp);
  UPB_LONGJMP(e->err, 1);
}
```

**File:** upb/json/encode.c (L72-81)
```c
static upb_Arena* jsonenc_arena(jsonenc* e) {
  /* Create lazily, since it's only needed for Any */
  if (!e->arena) {
    e->arena = upb_Arena_New();
    if (!e->arena) {
      jsonenc_err(e, "Out of memory");
    }
  }
  return e->arena;
}
```

**File:** upb/json/encode.c (L787-796)
```c
static size_t upb_JsonEncoder_Encode(jsonenc* const e,
                                     const upb_Message* const msg,
                                     const upb_MessageDef* const m,
                                     const size_t size) {
  if (UPB_SETJMP(e->err) != 0) return -1;

  jsonenc_msgfield(e, msg, m);
  if (e->arena) upb_Arena_Free(e->arena);
  return jsonenc_nullz(e, size);
}
```

**File:** php/ext/google/protobuf/php-upb.c (L6929-6962)
```c
static void jsonenc_any(jsonenc* e, const upb_Message* msg,
                        const upb_MessageDef* m) {
  const upb_FieldDef* type_url_f = upb_MessageDef_FindFieldByNumber(m, 1);
  const upb_FieldDef* value_f = upb_MessageDef_FindFieldByNumber(m, 2);
  upb_StringView type_url = upb_Message_GetFieldByDef(msg, type_url_f).str_val;
  upb_StringView value = upb_Message_GetFieldByDef(msg, value_f).str_val;
  const upb_MessageDef* any_m = jsonenc_getanymsg(e, type_url);
  const upb_MiniTable* any_layout = upb_MessageDef_MiniTable(any_m);
  upb_Arena* arena = jsonenc_arena(e);
  upb_Message* any = upb_Message_New(any_layout, arena);
  if (!any) {
    jsonenc_err(e, "Out of memory");
    return;
  }

  if (upb_Decode(value.data, value.size, any, any_layout, NULL, 0, arena) !=
      kUpb_DecodeStatus_Ok) {
    jsonenc_err(e, "Error decoding message in Any");
  }

  jsonenc_putstr(e, "{\"@type\":");
  jsonenc_string(e, type_url);

  if (upb_MessageDef_WellKnownType(any_m) == kUpb_WellKnown_Unspecified) {
    /* Regular messages: {"@type": "...","foo": 1, "bar": 2} */
    jsonenc_msgfields(e, any, any_m, false);
  } else {
    /* Well-known type: {"@type": "...","value": <well-known encoding>} */
    jsonenc_putstr(e, ",\"value\":");
    jsonenc_msgfield(e, any, any_m);
  }

  jsonenc_putstr(e, "}");
}
```

**File:** php/ext/google/protobuf/php-upb.c (L7326-7335)
```c
static size_t upb_JsonEncoder_Encode(jsonenc* const e,
                                     const upb_Message* const msg,
                                     const upb_MessageDef* const m,
                                     const size_t size) {
  if (UPB_SETJMP(e->err) != 0) return -1;

  jsonenc_msgfield(e, msg, m);
  if (e->arena) upb_Arena_Free(e->arena);
  return jsonenc_nullz(e, size);
}
```
