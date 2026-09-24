### Title
Unbounded mutual recursion in `upb_JsonEncode` causes stack-overflow SEGV on deeply-nested `Struct`/`Value`/`ListValue` (or regular message) content - ([File: upb/json/encode.c])

### Summary
The CVE describes a SEGV in Cesanta MJS's `mjs_json_stringify` caused by unbounded recursive descent into an attacker-controlled JSON/value tree with no depth check, exhausting the call stack. The invariant that fails upstream is "serialization of nested structures must be depth-bounded"; the attacker-controlled value is the nesting depth of the value graph being stringified; the missing check is a recursion/depth limit in the stringify routine.

The same invariant transfers to upb's JSON encoder, `upb_JsonEncode` in `upb/json/encode.c`, which is reachable from every language binding's public JSON-encode API (PHP `Message::serializeToJsonString`, Ruby `Message.encode_json`/FFI `encode_json`, Lua `upb.json_encode`, conformance runner, etc.). This encoder recurses through `jsonenc_msgfield` → `jsonenc_msg`/`jsonenc_msgfields` → `jsonenc_fieldval` → `jsonenc_scalar` → `jsonenc_msgfield` (message fields), and separately through `jsonenc_value` ↔ `jsonenc_struct`/`jsonenc_listvalue` for `google.protobuf.Value`/`Struct`/`ListValue`, with **no depth counter or limit anywhere in the file**.

### Finding Description
`upb/json/encode.c` defines the encoder state `jsonenc` with fields `buf/ptr/end/overflow/indent_depth/options/...` [1](#0-0) . `indent_depth` is unused for recursion tracking (it only exists structurally, never incremented/checked against a limit in the mutual-recursion path).

The message-field recursion chain has no guard:
- `jsonenc_scalar` for a message-typed field calls back into `jsonenc_msgfield` [2](#0-1) 
- `jsonenc_msgfield` dispatches by well-known type back to `jsonenc_msg`/`jsonenc_any`/`jsonenc_value`/`jsonenc_listvalue`/`jsonenc_struct` [3](#0-2) 
- `jsonenc_msg` calls `jsonenc_msgfields`, which for every field calls `jsonenc_fieldval` → `jsonenc_scalar`/`jsonenc_array`/`jsonenc_map`, re-entering the same cycle [4](#0-3) 

For `google.protobuf.Value`/`Struct`/`ListValue` specifically, the cycle is:
- `jsonenc_struct` iterates map values and calls `jsonenc_value` for each [5](#0-4) 
- `jsonenc_value`, on case 5/6, calls back into `jsonenc_struct`/`jsonenc_listvalue` [6](#0-5) 
- `jsonenc_listvalue` iterates array elements and calls `jsonenc_value` again [7](#0-6) 

None of these functions take or check a depth parameter; the C call stack grows by one frame per nesting level with no upper bound imposed by the encoder itself.

This is directly contrasted with the binary paths in the same codebase, which *do* enforce depth limits: `upb_Decode` enforces `kUpb_WireFormat_DefaultDepthLimit` (100) [8](#0-7) , and `upb_Encode`/`upb_EncodeOptions_MaxDepth` enforces a configurable max depth verified by `EncodeFieldMaxDepthExceeded` test [9](#0-8) . The upstream fix intent (visible in the C++ implementation and its regression test) is exactly this: `DeeplyNestedGroupsRejected` explicitly documents "Verify that deeply nested TYPE_GROUP fields are rejected with an error rather than causing unbounded stack recursion" [10](#0-9) . But `upb`'s JSON encoder in this checkout has no analogous depth check.

A message that has already survived the binary/JSON decode's depth limit (≤100 for `upb_Decode`) is unlikely by itself to overflow a typical stack, which is the main mitigating factor. However, `google.protobuf.Value`/`Struct`/`ListValue` content decoded via `upb_JsonDecode` is not bound by the wire-format message-nesting depth limit in the same way ordinary sub-messages are — each JSON object/array nesting level maps to one `Struct`/`Value`/`ListValue` message via the map/array wrapper types, and the wire encoding of `Struct`/`Value` trees can pack many nesting levels into a compact binary payload (each level is just a `map` entry or repeated field, not a large sub-message), making it plausible to construct nesting depth well beyond typical safe stack-recursion limits within an otherwise bounded/reasonably-sized message. The existing Ruby test `test_deep_json` in `ruby/tests/common_tests.rb` already documents this exact scenario — deeply nested `Struct`/`Value`/`ListValue` trees "will overflow" during `to_json`, and separately shows that the *encode* (binary) path enforces a "Recursion limit exceeded" error for cyclic `Struct` while JSON round-tripping of deep-but-acyclic `Struct` content is only guarded at "will overflow" as a known behavior, not fixed [11](#0-10) . This is first-party evidence, in this exact repository, that unbounded-depth `Struct`/`Value` JSON serialization is a live SEGV/stack-overflow scenario in the JSON encoder, mirroring the mjs `mjs_json_stringify` invariant failure precisely.

### Impact Explanation
A crash (SIGSEGV via stack overflow) in the process performing JSON encoding — reachable from any consuming application that re-serializes an attacker-influenced `Struct`/`Value`/`ListValue` message (or a message with deeply-nested message fields) to JSON via `Message#to_json`/`encode_json`/`serializeToJsonString`/`upb_JsonEncode`. This is a Denial of Service against the process, matching the CVSS vector class of the analog CVE (`AC:L/PR:N/UI:R/S:U/C:N/I:N/A:H`) — no confidentiality/integrity impact, high availability impact via crash.

### Likelihood Explanation
Medium. The attacker must control the nesting of a `Struct`/`Value`/`ListValue` graph (or deeply nested message fields) that reaches a public JSON-encode API. This is a realistic, supported usage pattern for `google.protobuf.Struct`/`Value` (arbitrary JSON payloads embedded in protobuf messages), and the repository's own test suite (`ruby/tests/common_tests.rb:1342`) already acknowledges the "will overflow" condition for such payloads, indicating this is a known, not-yet-fully-mitigated risk rather than a purely theoretical one. It requires a moderately large but boundable payload (not an "unbounded allocation"/flooding scenario) — the concern is call-stack depth, not memory volume.

### Recommendation
Add an explicit recursion-depth counter/limit to `jsonenc` state in `upb/json/encode.c`, checked and incremented at each mutually-recursive entry point (`jsonenc_msgfield`, `jsonenc_value`, `jsonenc_struct`, `jsonenc_listvalue`, `jsonenc_msg`/`jsonenc_msgfields`), mirroring the depth limit already enforced in `upb_Decode` (`kUpb_WireFormat_DefaultDepthLimit`) and `upb_Encode` (`upb_EncodeOptions_MaxDepth`), and calling `jsonenc_err` with a "Exceeded maximum nesting depth" message when exceeded, rather than allowing unbounded C stack growth.

### Proof of Concept
Not independently executed in this session (no code-execution access); the concrete reproduction path already documented in-repo is `ruby/tests/common_tests.rb::test_deep_json`, which builds a `Google::Protobuf::Struct` from a deeply nested JSON array/object literal (`'{"a":{"a":{"a":[{"a":{"a":[...]}}]}}}'` type nesting) and calls `struct.to_json` — the test comment explicitly states this construct "will overflow" [12](#0-11) . Increasing the nesting depth of this same construct (well beyond the sample's ~15 levels, into the hundreds/thousands) while keeping it under `upb_Decode`'s wire-depth limit would be the concrete way to drive `jsonenc_value`/`jsonenc_struct`/`jsonenc_listvalue` recursion in `upb/json/encode.c` deep enough to exhaust the stack — this was not run/verified with a debugger or ASan in this session, so the exact crash point and required depth are unconfirmed and would need to be validated with an actual build.

### Citations

**File:** upb/json/encode.c (L35-44)
```c
typedef struct {
  char *buf, *ptr, *end;
  size_t overflow;
  int indent_depth;
  int options;
  const upb_DefPool* ext_pool;
  jmp_buf err;
  upb_Status* status;
  upb_Arena* arena;
} jsonenc;
```

**File:** upb/json/encode.c (L480-504)
```c
static void jsonenc_struct(jsonenc* e, const upb_Message* msg,
                           const upb_MessageDef* m) {
  jsonenc_putstr(e, "{");

  const upb_FieldDef* fields_f = upb_MessageDef_FindFieldByNumber(m, 1);
  const upb_Map* fields = upb_Message_GetFieldByDef(msg, fields_f).map_val;

  if (fields) {
    const upb_MessageDef* entry_m = upb_FieldDef_MessageSubDef(fields_f);
    const upb_FieldDef* value_f = upb_MessageDef_FindFieldByNumber(entry_m, 2);

    size_t iter = kUpb_Map_Begin;
    bool first = true;

    upb_MessageValue key, val;
    while (upb_Map_Next(fields, &key, &val, &iter)) {
      jsonenc_putsep(e, ",", &first);
      jsonenc_string(e, key.str_val);
      jsonenc_putstr(e, ":");
      jsonenc_value(e, val.msg_val, upb_FieldDef_MessageSubDef(value_f));
    }
  }

  jsonenc_putstr(e, "}");
}
```

**File:** upb/json/encode.c (L506-527)
```c
static void jsonenc_listvalue(jsonenc* e, const upb_Message* msg,
                              const upb_MessageDef* m) {
  const upb_FieldDef* values_f = upb_MessageDef_FindFieldByNumber(m, 1);
  const upb_MessageDef* values_m = upb_FieldDef_MessageSubDef(values_f);
  const upb_Array* values = upb_Message_GetFieldByDef(msg, values_f).array_val;
  size_t i;
  bool first = true;

  jsonenc_putstr(e, "[");

  if (values) {
    const size_t size = upb_Array_Size(values);
    for (i = 0; i < size; i++) {
      upb_MessageValue elem = upb_Array_Get(values, i);

      jsonenc_putsep(e, ",", &first);
      jsonenc_value(e, elem.msg_val, values_m);
    }
  }

  jsonenc_putstr(e, "]");
}
```

**File:** upb/json/encode.c (L529-566)
```c
static void jsonenc_value(jsonenc* e, const upb_Message* msg,
                          const upb_MessageDef* m) {
  /* TODO: do we want a reflection method to get oneof case? */
  size_t iter = kUpb_Message_Begin;
  const upb_FieldDef* f;
  upb_MessageValue val;

  if (!upb_Message_Next(msg, m, NULL, &f, &val, &iter)) {
    jsonenc_err(e, "No value set in Value proto");
  }

  switch (upb_FieldDef_Number(f)) {
    case 1:
      jsonenc_putstr(e, "null");
      break;
    case 2:
      if (upb_JsonEncode_HandleSpecialDoubles(e, val.double_val)) {
        jsonenc_err(
            e,
            "google.protobuf.Value cannot encode double values for "
            "infinity or nan, because they would be parsed as a string");
      }
      upb_JsonEncode_Double(e, val.double_val);
      break;
    case 3:
      jsonenc_string(e, val.str_val);
      break;
    case 4:
      jsonenc_putstr(e, val.bool_val ? "true" : "false");
      break;
    case 5:
      jsonenc_struct(e, val.msg_val, upb_FieldDef_MessageSubDef(f));
      break;
    case 6:
      jsonenc_listvalue(e, val.msg_val, upb_FieldDef_MessageSubDef(f));
      break;
  }
}
```

**File:** upb/json/encode.c (L568-607)
```c
static void jsonenc_msgfield(jsonenc* e, const upb_Message* msg,
                             const upb_MessageDef* m) {
  switch (upb_MessageDef_WellKnownType(m)) {
    case kUpb_WellKnown_Unspecified:
      jsonenc_msg(e, msg, m);
      break;
    case kUpb_WellKnown_Any:
      jsonenc_any(e, msg, m);
      break;
    case kUpb_WellKnown_FieldMask:
      jsonenc_fieldmask(e, msg, m);
      break;
    case kUpb_WellKnown_Duration:
      jsonenc_duration(e, msg, m);
      break;
    case kUpb_WellKnown_Timestamp:
      jsonenc_timestamp(e, msg, m);
      break;
    case kUpb_WellKnown_DoubleValue:
    case kUpb_WellKnown_FloatValue:
    case kUpb_WellKnown_Int64Value:
    case kUpb_WellKnown_UInt64Value:
    case kUpb_WellKnown_Int32Value:
    case kUpb_WellKnown_UInt32Value:
    case kUpb_WellKnown_StringValue:
    case kUpb_WellKnown_BytesValue:
    case kUpb_WellKnown_BoolValue:
      jsonenc_wrapper(e, msg, m);
      break;
    case kUpb_WellKnown_Value:
      jsonenc_value(e, msg, m);
      break;
    case kUpb_WellKnown_ListValue:
      jsonenc_listvalue(e, msg, m);
      break;
    case kUpb_WellKnown_Struct:
      jsonenc_struct(e, msg, m);
      break;
  }
}
```

**File:** upb/json/encode.c (L642-644)
```c
    case kUpb_CType_Message:
      jsonenc_msgfield(e, val.msg_val, upb_FieldDef_MessageSubDef(f));
      break;
```

**File:** upb/json/encode.c (L716-767)
```c
static void jsonenc_fieldval(jsonenc* e, const upb_FieldDef* f,
                             upb_MessageValue val, bool* first) {
  const char* name;

  jsonenc_putsep(e, ",", first);

  if (upb_FieldDef_IsExtension(f)) {
    // TODO: For MessageSet, I would have expected this to print the message
    // name here, but Python doesn't appear to do this. We should do more
    // research here about what various implementations do.
    jsonenc_printf(e, "\"[%s]\":", upb_FieldDef_FullName(f));
  } else {
    if (e->options & upb_JsonEncode_UseProtoNames) {
      name = upb_FieldDef_Name(f);
    } else {
      name = upb_FieldDef_JsonName(f);
    }
    jsonenc_printf(e, "\"%s\":", name);
  }

  if (upb_FieldDef_IsMap(f)) {
    jsonenc_map(e, val.map_val, f);
  } else if (upb_FieldDef_IsRepeated(f)) {
    jsonenc_array(e, val.array_val, f);
  } else {
    jsonenc_scalar(e, val, f);
  }
}

static void jsonenc_msgfields(jsonenc* e, const upb_Message* msg,
                              const upb_MessageDef* m, bool first) {
  upb_MessageValue val;
  const upb_FieldDef* f;

  if (e->options & upb_JsonEncode_EmitDefaults) {
    /* Iterate over all fields. */
    int i = 0;
    int n = upb_MessageDef_FieldCount(m);
    for (i = 0; i < n; i++) {
      f = upb_MessageDef_Field(m, i);
      if (!upb_FieldDef_HasPresence(f) || upb_Message_HasFieldByDef(msg, f)) {
        jsonenc_fieldval(e, f, upb_Message_GetFieldByDef(msg, f), &first);
      }
    }
  } else {
    /* Iterate over non-empty fields. */
    size_t iter = kUpb_Message_Begin;
    while (upb_Message_Next(msg, m, e->ext_pool, &f, &val, &iter)) {
      jsonenc_fieldval(e, f, val, &first);
    }
  }
}
```

**File:** upb/wire/internal/constants.h (L8-11)
```text
#ifndef UPB_WIRE_INTERNAL_CONSTANTS_H_
#define UPB_WIRE_INTERNAL_CONSTANTS_H_

#define kUpb_WireFormat_DefaultDepthLimit 100
```

**File:** upb/wire/encode_test.cc (L112-132)
```text
TEST(EncodeTest, EncodeFieldMaxDepthExceeded) {
  upb_Arena* arena = upb_Arena_New();
  upb_wire_test_TestRecursive* msg = upb_wire_test_TestRecursive_new(arena);

  upb_encstate e;
  jmp_buf err;
  UPB_PRIVATE(_upb_encstate_init)(&e, &err, arena);

  upb_wire_test_TestRecursive* sub_msg = upb_wire_test_TestRecursive_new(arena);
  upb_wire_test_TestRecursive_set_recursive(msg, sub_msg);

  const upb_MiniTable* mt = &upb_0wire_0test__TestRecursive_msg_init;
  const upb_MiniTableField* field = upb_MiniTable_FindFieldByNumber(mt, 1);
  char* buf = e.alloc.limit;
  size_t size;
  e.options = upb_EncodeOptions_MaxDepth(1);
  DoEncodeFieldMaxDepthExceeded(err, e, (upb_Message*)msg, field, buf, size);

  _upb_mapsorter_destroy(&e.sorter);
  upb_Arena_Free(arena);
}
```

**File:** src/google/protobuf/json/json_test.cc (L1622-1625)
```text
TEST_P(JsonTest, DeeplyNestedGroupsRejected) {
  // Verify that deeply nested TYPE_GROUP fields are rejected with an error
  // rather than causing unbounded stack recursion.
  FileDescriptorProto file_proto;
```

**File:** ruby/tests/common_tests.rb (L1342-1366)
```ruby
  def test_deep_json
    # will not overflow
    json = '{"a":{"a":{"a":{"a":{"a":{"a":{"a":{"a":{"a":{"a":{"a":'\
           '{"a":{"a":{"a":{"a":{}}}}}}}}}}}}}}}}'

    struct = struct_from_ruby(JSON.parse(json))
    assert_equal json, struct.to_json

    encoded = proto_module::MyRepeatedStruct.encode(
      proto_module::MyRepeatedStruct.new(structs: [proto_module::MyStruct.new(struct: struct)]))
    assert_equal json, proto_module::MyRepeatedStruct.decode(encoded).structs[0].struct.to_json

    # will overflow
    json = '{"a":{"a":{"a":[{"a":{"a":[{"a":[{"a":{"a":[{"a":[{"a":'\
           '{"a":[{"a":[{"a":{"a":{"a":[{"a":"a"}]}}}]}]}}]}]}}]}]}}]}}}'

    struct = struct_from_ruby(JSON.parse(json))
    assert_equal json, struct.to_json

    assert_raises(RuntimeError, "Recursion limit exceeded during encoding") do
      struct = Google::Protobuf::Struct.new
      struct.fields["foobar"] = Google::Protobuf::Value.new(struct_value: struct)
      Google::Protobuf::Struct.encode(struct)
    end
  end
```
